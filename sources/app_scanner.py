"""Finds job applications in Gmail that aren't in the sheet yet and adds them.

Reading the confirmation email is `core.confirmation_parser`'s job; this
module decides what the sheet should do about what it read. The two were one
609-line file, which meant the parsing rules could only be tested by running
this — a live Gmail search and a live sheet read — and left the comparison
logic buried under forty regexes.

Rows that can't be parsed confidently are reported rather than guessed at —
a wrong row is worse than a missing one, since you'd have to find and fix it.
"""

from email.utils import parsedate_to_datetime

from store import gmail_client
from core import linkedin_helper
from store import sheets
from core import status as status_mod
from core.config import HEADERS
from core.company_match import normalize
from core.confirmation_parser import REASONS, parse
from core.date_utils import parse_date
from sources.link_finder import CONFIRMATION_PHRASES


# Applying to the same role twice, months apart, is two applications. Kroll
# re-posted "Analyst, Financial Instruments & Technology" in November under a
# new requisition; the July row — rejected in September — matched the key and
# swallowed the confirmation, so the second application was never logged. The
# gap has to be wider than dedupe's own window, so a confirmation that lands a
# few days after the row it belongs to still collapses into it.
REAPPLY_GAP_DAYS = 60


def _reapplied(msg: dict, applied_dates: list) -> bool:
    """Is this confirmation a fresh application to a role already on the sheet?"""
    if not applied_dates:
        return False
    try:
        sent = parsedate_to_datetime(msg["date"]).date()
    except (KeyError, TypeError, ValueError):
        return False
    return all((sent - d).days > REAPPLY_GAP_DAYS for d in applied_dates)


def _tracked(company: str, role: str, existing: list[dict]) -> bool:
    """Is this company/role already on the sheet, allowing for a longer title?

    Uses dedupe's title comparison rather than an exact match: the sheet said
    "Debt Analyst" and JLL's mail said "Debt Analyst, Hotels - Capital
    Markets", which is the same application by any reading.
    """
    from core.company_match import _core as core
    from core.job_title import _role_match

    target = core(company).replace(" ", "") or normalize(company)
    for r in existing:
        name = r.get("Company", "")
        if (core(name).replace(" ", "") or normalize(name)) != target:
            continue
        if _role_match(role, r.get("Position", "")):
            return True
    return False


def scan(days_back: int = 730, dry_run: bool = False,
         max_results: int = 4000) -> None:
    phrase_query = " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES)
    # Not `in:inbox`: confirmations get archived, auto-filtered into labels or
    # swept into tabs, and those applications still happened. The same
    # restriction was removed from rejection_scanner for the same reason.
    query = f"newer_than:{days_back}d ({phrase_query}) -from:nyu.edu"
    print(f"Searching Gmail: {query}\n")

    # Bodies cost an extra round trip per message, but they are where the job
    # title actually is — without them every generic confirmation lands in the
    # sheet as "Analyst".
    msgs = gmail_client.search_messages(query, max_results=max_results,
                                        include_body=True)
    print(f"Found {len(msgs)} candidate confirmation emails.")

    existing = sheets.list_rows()
    # Keyed on the distinctive core, not the full name: the sheet says "BMO
    # Capital Markets" while the confirmation says "BMO", and keying on the
    # full name let that through as a second row for the same application.
    from core.company_match import _core as _company_core

    def _key(company: str, role: str) -> tuple[str, str]:
        return (_company_core(company).replace(" ", "") or normalize(company),
                normalize(role))

    # A row whose company you corrected keeps answering to the name it was
    # corrected away from: the ATS goes on sending under "hyperiongrp" long
    # after the row says "Howden", and without the alias the confirmation
    # matched nothing and came back as a new row.
    from core import company_aliases

    existing_keys = set()
    # Kept per key so a confirmation months newer than the row it matches can
    # be told apart from one that simply arrived late — see _reapplied.
    existing_dates: dict[tuple, list] = {}
    for r in existing:
        applied = parse_date(r.get("Applied Draes", ""))
        for k in (_key(r["Company"], r["Position"]),
                  *(_key(alias, r["Position"])
                    for alias in company_aliases.aliases_of(r["Company"]))):
            existing_keys.add(k)
            if applied:
                existing_dates.setdefault(k, []).append(applied)
    # Compared on the distinctive core, so "bp Global" collides with the "bp"
    # row you already have, and "Fitch Group" with "Fitch" — the sender's
    # display name spells companies differently from the sheet.
    from core.company_match import _core
    # Space-stripped too: a company taken from a domain label has no spaces
    # ("chimerasecurities"), so it never matched the sheet's "Chimera
    # Securities" and re-imported as a duplicate row.
    existing_companies = set()
    for r in existing:
        if not r["Company"].strip():
            continue
        for name in (r["Company"], *company_aliases.aliases_of(r["Company"])):
            for c in (normalize(name), _core(name)):
                if c:
                    existing_companies.add(c)
                    existing_companies.add(c.replace(" ", ""))

    # Imported here, not at module scope: rejection_scanner imports this
    # module's siblings and pulling it in at the top makes the cycle real.
    from sources import rejection_scanner as _rejection_scanner

    new_rows = {}
    # Keyed by the parser's own reason, not one undifferentiated "unparsed"
    # list. The old list printed fifteen subject lines and a count, which told
    # you nothing you could act on: a job alert the parser correctly ignored
    # looked exactly like an application it had failed to read.
    skipped: dict[str, list] = {}
    rejections = []
    for m in msgs:
        read = parse(m)
        if not read:
            skipped.setdefault(read.reason, []).append(m)
            continue
        company, role = read.company, read.role
        # The confirmation query matches on phrases like "thank you for your
        # interest", which plenty of rejections close with: JLL's
        # "Application Update" declined the Debt Analyst role and was logged
        # as a brand-new application for it, at the rejection's own date.
        # A rejection for a role already on the sheet is therefore skipped —
        # the rejection scan closes that row, this one would only duplicate
        # it. A rejection for a role that is *not* on the sheet still counts:
        # it is the only trace of an application nothing else recorded, so it
        # goes in already closed rather than as a fresh "applied".
        rejected = bool(_rejection_scanner._is_real_rejection(m))
        if rejected and _tracked(company, role, existing):
            rejections.append(m)
            continue
        # Built with _key(), so it must be compared with _key(). Keying the
        # incoming side on normalize() instead meant "MasterCard People
        # Services" never collided with the "MasterCard" row already in the
        # sheet, and the same application was logged twice.
        key = _key(company, role)
        if key in new_rows:
            continue
        if key in existing_keys and not _reapplied(m, existing_dates.get(key, [])):
            continue
        # With no role parsed there's nothing to distinguish this from an
        # application already tracked at the same company, so skip it rather
        # than create a likely duplicate under a placeholder role.
        if not role and any(
            form in existing_companies
            for form in (normalize(company), _core(company),
                         normalize(company).replace(" ", ""),
                         _core(company).replace(" ", ""))
        ):
            continue
        try:
            date = parsedate_to_datetime(m["date"]).date()
        except Exception:
            continue
        new_rows[key] = {
            "company": company,
            "role": role or "Analyst",
            "date": f"{date.month}/{date.day}/{date.year}",
            "subject": m["subject"],
            "known_company": normalize(company) in existing_companies,
            # This row's date is the day it was *declined*, not applied; the
            # application itself left no mail behind to date it by.
            "rejected": rejected,
        }

    print(f"\n{len(new_rows)} application(s) not yet in the sheet:")
    for v in sorted(new_rows.values(), key=lambda x: x["date"]):
        flag = "" if v["known_company"] else "  [new company]"
        print(f"  {v['date']:10s} | {v['company'][:35]:35s} | {v['role'][:40]}{flag}")

    if rejections:
        print(f"\n{len(rejections)} rejection(s) skipped — the rejection scan "
              f"closes those rows, this one only opens them:")
        for m in rejections[:10]:
            print(f"  \"{m['subject'][:80]}\"")

    if skipped:
        # Mail that records no application is the parser working. Only the
        # rest is a miss, so the two are counted apart — lumping them is what
        # made the old figure look like a quarter of the mailbox was unreadable.
        clean = ("noise", "not-an-application")
        misses = sum(len(v) for r, v in skipped.items() if r not in clean)
        ignored = sum(len(v) for r, v in skipped.items() if r in clean)
        print(f"\n{ignored} email(s) recorded no application (job alerts, "
              f"signups); {misses} could not be read:")
        for reason, group in sorted(skipped.items(), key=lambda kv: -len(kv[1])):
            mark = " " if reason in clean else "!"
            print(f"  {mark} {len(group):4d}  {reason:22s} {REASONS[reason]}")
            if reason in clean:
                continue
            for m in group[:6]:
                print(f"            \"{m['subject'][:72]}\"")
            if len(group) > 6:
                print(f"            ... and {len(group) - 6} more")

    if dry_run:
        print("\n(dry-run — nothing written)")
        return
    if not new_rows:
        return

    sheets.ensure_headers()
    values = []
    for v in new_rows.values():
        row = {
            "Company": v["company"],
            "Position": v["role"],
            "Applied Draes": v["date"],
            "Status": status_mod.display(
                status_mod.REJECTED if v.get("rejected") else status_mod.APPLIED),
            "Result": "Rejected" if v.get("rejected") else "",
            "Notes": "Auto-added from Gmail confirmation email",
        }
        values.append([row.get(h, "") for h in HEADERS])

    # Writes to an explicit row number rather than values().append() +
    # INSERT_ROWS, for the same reason sheets.append_job_row() does: that
    # call auto-detects "the table" from the given range and inserts right
    # after wherever it decides that ends, which is not necessarily the
    # sheet's actual last row — it has silently spliced a new row into the
    # middle of real data before. One batched write (not sheets.append_job_row
    # per row, which would cost one round trip per application) starting at
    # the true next row has no such ambiguity.
    svc = sheets._service()
    next_row = len(sheets.list_rows()) + 2  # +1 for the header, +1 to land past the last row
    svc.spreadsheets().values().update(
        spreadsheetId=sheets.SHEET_ID,
        range=f"{sheets.SHEET_TAB}!A{next_row}",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()
    print(f"\nAdded {len(values)} row(s) to the sheet.")

    # A LinkedIn search link, not a picked contact: automating who to email
    # would mean either scraping LinkedIn (against its ToS, and a real risk
    # to the account) or a paid search API this project doesn't have. This is
    # the part that's actually safe to automate — so it's here waiting
    # instead of something you have to come back and ask for.
    print("\nLinkedIn search for a contact at each new company:")
    for v in sorted(new_rows.values(), key=lambda x: x["date"]):
        url = linkedin_helper.people_search_url(v["company"])
        print(f"  {v['company']:35s} {url}")

    # No draft is written here on purpose. This used to create one per new
    # application with an empty To: field, on the theory that a written email
    # waiting in Drafts was worth more than nothing. It was not — they piled
    # up unaddressed, indistinguishable from real drafts, and a reach-out with
    # no recipient is not a reach-out. The standing rule is that every draft
    # has a real person on it.
    #
    # Finding that person needs a live web search, which plain Python against
    # Gmail and Sheets cannot do. `cli.py auto-draft` (auto_draft.py) is the
    # piece that can: it researches a real mid-level contact and writes the
    # email in one Muse request. It needs MODEL_API_KEY set.
    print("\nTo research a contact and write the email for these:")
    print("  ./venv/bin/python cli.py auto-draft --limit "
          f"{len(new_rows)} --write-drafts")
