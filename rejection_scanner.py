"""Scans Gmail for rejection emails and matches them back to rows still
marked 'applied' in the sheet, updating Status to 'rejected' automatically.

Matching is deliberately conservative: a row is only auto-updated when
exactly one still-'applied' row's company name appears in the email. If
multiple rows share a company name, it's reported instead of guessed at.
"""

import json
import os
import re

import corrections
import gmail_client
import interview_signals
import sheets
from config import ME_RE
from date_utils import parse_date
from company_match import company_mentioned, company_in_sender
import status as status_mod
from link_finder import _disambiguate, _role_conflicts

REJECTION_PHRASES = [
    "unfortunately",
    "not moving forward",
    "will not be moving forward",
    "decided to move forward with other",
    "decided to proceed with other candidates",
    "other candidates",
    "not selected",
    "position has been filled",
    "pursue other candidates",
    "will not be proceeding",
    "is now closed",
    "no longer under consideration",
    "not be moving forward",
    "moving forward with other",
    "decided not to move forward",
    "filled this position",
    "position is closed",
    "not to continue with your application",
    "decided not to continue",
    "another candidate",
    "other applicants",
    "will not be advancing",
    "not be advancing",
    "regret to inform",
    "unable to offer you",
    "not progressing",
    "more closely align",
    "no longer being considered",
    "decided to go with",
"not been selected",
    "different direction",
    "unable to move you forward",
    "proceed with another candidate",
    "we regret",
]

# Gmail's search only tells us a phrase appears *somewhere*; confirmation
# emails routinely contain rejection-sounding boilerplate ("if you are not
# selected..."). These are the phrases that only appear in a real rejection,
# and one of them must be present in the body before a row is marked.
DECISIVE_PHRASES = [
    "not moving forward",
    "will not be moving forward",
    "not be moving forward",
    "decided to move forward with other",
    "moving forward with other",
    "decided to proceed with other candidates",
    "pursue other candidates",
    "not selected",
    "position has been filled",
    "filled this position",
    "position is closed",
    "is now closed",
    "no longer under consideration",
    "will not be proceeding",
    "decided not to move forward",
    "not to move forward with your",
    "other candidates whose",
    # Seen verbatim in real rejections here — Conning, MUFG, StoneX and
    # others each word it differently, so coverage has to be broad.
    "not to continue with your application",
    "decided not to continue",
    "another candidate that better",
    "another candidate whose",
    "other candidates at this time",
    "other applicants whose",
    "will not be advancing",
    "not be advancing your",
    "regret to inform",
    "unable to offer you",
    "not progressing with your",
    "more closely align",
    "no longer being considered",
    "not be considered further",
    "decided to go with another",
    "chosen to move forward with another",
    "selected another candidate",
    "we have decided to pursue",
    # Fanatics' wording, which names the chosen group rather than declining
    # outright: "we've decided to move forward with candidates whose
    # experience most closely aligns". The list had "decided to move forward
    # with other", which this does not contain.
    "move forward with candidates whose",
    # Kroll's wording, which reports the role's state rather than a decision
    # about you: "the position you applied for ... has now been filled and we
    # are no longer considering applications for the role". The list had
    # "position has been filled" and "filled this position", neither of which
    # matches "has now been filled".
    "has now been filled",
    "no longer considering applications",
    # Huntington's Workday template: "although your qualifications are
    # impressive, at this time we have chosen not to pursue your application
    # further". The list had "pursue other candidates" and "we have decided
    # to pursue", neither of which matches a firm declining without naming
    # anyone else — so the row stayed 'applied' and outreach was drafted to
    # someone on a desk that had already said no.
    "not to pursue your application",
    "chosen not to pursue",
    "decided not to pursue",
    "not to proceed with your",
    "will not be extending",
# Wording found by sampling rejections these rules were missing.
    "not been selected for",
    "not been selected to",
    "unable to move you forward",
    "different direction at this time",
    "proceed with another candidate",
    "regret to let you know",
    "were not successful",
    "was not successful",
    # Flagstar's MD wrote this by hand and nothing above matched it — the list
    # only had "...with other candidates", never the singular "another".
    "move forward with another candidate",
    "moving forward with another candidate",
    "move forward with a different candidate",
    "went with another candidate",
    "offer to another candidate",
    "filled the position with another",
]

# Subject shapes that mean "we are writing about your application", without
# any decisive phrase appearing in the subject itself. The phrase search below
# only retrieves mail whose *body text* Gmail indexed against one of
# REJECTION_PHRASES; plenty of ATS rejections word the body differently and
# were never downloaded, so the rules never got to judge them. These widen
# what gets fetched — `_is_real_rejection` still decides.
SUBJECT_QUERIES = [
    'subject:"application status"',
    'subject:"update on your application"',
    'subject:"your application"',
    'subject:"application update"',
    'subject:"regarding your application"',
    'subject:"status of your application"',
    'subject:"candidate update"',
    'subject:"position filled"',
    'subject:"thank you for your interest"',
    'subject:"following up on your application"',
]

# Confirmation emails describe rejection hypothetically ("if you are not
# selected at this stage, we will keep your application on file"). A decisive
# phrase in that context is boilerplate, not a decision about this candidate.
_CONDITIONAL_LEAD_RE = re.compile(
    r"(if|should|unless|whether|in case|in the event)\b[^.]{0,60}$", re.I
)


# LinkedIn never says no in words. Its rejection notice reads "Your update
# from <company>" with no decisive phrase anywhere in the visible text — the
# only tell is the tracking parameter on its own links, which literally spells
# out `email_jobs_application_rejected`. Adore Me's rejection sat undetected
# because of this, and LinkedIn is where most of these applications go.
_LINKEDIN_REJECTED_RE = re.compile(
    r"email[_%-]?5?f?jobs[_%-]?5?f?application[_%-]?5?f?rejected", re.I)


_MODEL = None
_MODEL_TRIED = False
# CV F1 is ~0.77, so the model is a useful net but not trustworthy at the
# margin. Only a very confident prediction is allowed to mark a row, since a
# false positive silently closes a live application.
MODEL_THRESHOLD = 0.90


def _model():
    global _MODEL, _MODEL_TRIED
    if not _MODEL_TRIED:
        _MODEL_TRIED = True
        try:
            import rejection_model
            _MODEL = rejection_model.load()
        except Exception:
            _MODEL = None
    return _MODEL


def _is_real_rejection(msg: dict) -> str | None:
    """Returns the decisive phrase found in the body, or None."""
    raw = msg.get("body", "") or ""
    if _LINKEDIN_REJECTED_RE.search(raw):
        return "linkedin application_rejected notice"

    text = interview_signals.plain_text(raw)
    for phrase in DECISIVE_PHRASES:
        idx = text.find(phrase)
        while idx >= 0:
            lead = text[max(0, idx - 60):idx]
            if not _CONDITIONAL_LEAD_RE.search(lead):
                return phrase
            idx = text.find(phrase, idx + 1)

    # Nothing in the phrase list matched. Ask the learned model, which
    # generalizes to ATS wording nobody thought to enumerate.
    m = _model()
    if m is not None:
        import rejection_model
        p = rejection_model.predict_proba(m, msg.get("body", ""))
        if p >= MODEL_THRESHOLD:
            return f"model p={p:.2f}"
    return None


# Which row each rejection email has already closed.
#
# One email can only ever be about one application, but Wolfe's rejection is
# titled "Equity Sales Assistant, Quantitative Sales" — it names two roles, so
# once the row it really belonged to was closed, the next run matched the only
# remaining open Wolfe row and closed that too, then the next one after that.
# Three separate rows were wrongly marked interview-rejected this way. Claims
# are remembered so a message can never close a second row.
CLAIMS_FILE = os.path.join("logs", "rejection_claims.json")


def _load_claims() -> dict:
    try:
        with open(CLAIMS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_claims(claims: dict) -> None:
    os.makedirs(os.path.dirname(CLAIMS_FILE), exist_ok=True)
    with open(CLAIMS_FILE, "w") as f:
        json.dump(claims, f, indent=1, sort_keys=True)


def rejection_status(msg: dict) -> tuple[str, str]:
    """Classifies a confirmed rejection as plain or post-interview.

    Returns (canonical status, evidence). Losing after a conversation is a
    different outcome from being screened out, and the sheet colors them
    differently (yellow vs red), so it is worth the extra thread fetch.
    """
    ev = interview_signals.post_interview_evidence(msg)
    if ev:
        return status_mod.INTERVIEW_REJECTED, f"wording \"{ev}\""

    # The rejection itself may be terse ("we went another direction") while the
    # invite that scheduled the call sits earlier in the same thread.
    thread_id = msg.get("threadId")
    if thread_id:
        try:
            thread = gmail_client.get_thread(thread_id)
        except Exception:
            thread = []
        ev = interview_signals.thread_interview_evidence(
            thread, ME_RE, before_id=msg.get("id")
        )
        if ev:
            return status_mod.INTERVIEW_REJECTED, f"thread {ev}"

    return status_mod.REJECTED, ""


def scan(days_back: int = 180, dry_run: bool = False, since_date=None,
         max_results: int = 10000) -> None:
    phrase_query = " OR ".join(
        [f'"{p}"' for p in REJECTION_PHRASES] + SUBJECT_QUERIES)
    # Not `in:inbox`: rejections get archived, auto-filtered into labels, or
    # swept up by tabs, and those are still rejections.
    query = f"newer_than:{days_back}d ({phrase_query})"

    print(f"Searching Gmail: {query}\n")
    # Explicit cap: the default (100) silently truncated a 200+ result set,
    # so most rejections were never examined.
    messages = gmail_client.search_messages(query, include_body=True, max_results=max_results)
    print(f"Found {len(messages)} candidate rejection emails.\n")

    applied_rows = [
        r for r in sheets.list_rows()
        if status_mod.is_open(r.get("Status", ""))
    ]
    if since_date:
        applied_rows = [
            r for r in applied_rows
            if (d := parse_date(r.get("Applied Draes", ""))) and d >= since_date
        ]
        print(f"Restricting to {len(applied_rows)} applied row(s) on/after {since_date}.\n")

    claims = _load_claims()
    already_claimed = []
    overruled = []
    updated = []
    ambiguous = []
    unmatched = []
    not_rejections = []

    for msg in messages:
        # 'from' is deliberately excluded: ATS vendor domains (myworkday.com,
        # oraclecloud.com, icims.com) made unrelated companies match.
        phrase = _is_real_rejection(msg)
        if not phrase:
            not_rejections.append(msg)
            continue
        haystack = f"{msg['subject']} {msg['snippet']}".lower()
        # Sender domain is checked too: a hiring manager writing from their own
        # address ("Flagstar Analyst Position" from @flagstar.com) never spells
        # the company the way the sheet does.
        matches = [
            r for r in applied_rows
            if company_mentioned(r["Company"], haystack)
            or company_in_sender(r["Company"], msg.get("from", ""))
        ]
        # Several rows at one company (e.g. two Salesforce applications) used
        # to be skipped outright; the email's subject usually names which role
        # it is about, so try that before giving up.
        if len(matches) > 1:
            resolved = _disambiguate(matches, msg)
            if resolved is not None:
                matches = [resolved]

        # A single remaining match is not proof: if the row this email is really
        # about was already marked rejected, the only 'applied' row left at
        # that company will match by company name alone. Require that the
        # email does not name a clearly different role.
        if len(matches) == 1 and _role_conflicts(matches[0], msg):
            ambiguous.append((matches, msg))
            continue

        if len(matches) == 1:
            row = matches[0]
            # This email already closed a different row on an earlier run, so
            # it cannot be about this one too.
            claimed = claims.get(msg["id"])
            if claimed is not None and claimed != row["row_number"]:
                already_claimed.append((row, msg, claimed))
                continue
            # You have already ruled on this row. Wording that once fooled the
            # scanner will fool it identically on every future run, so the
            # only thing that stops the correction being undone is refusing
            # to look.
            if corrections.is_locked(row, "Status"):
                overruled.append((row, corrections.locked(row, "Status")))
                continue
            new_status, evidence = rejection_status(msg)
            if dry_run:
                updated.append((row, msg, new_status, evidence))
                continue
            label = ("post-interview rejection"
                     if new_status == status_mod.INTERVIEW_REJECTED else "rejection")
            note = f"Auto-detected {label} email ({msg['date']}): \"{msg['subject']}\""
            if evidence:
                note += f" [{evidence}]"
            existing_notes = row.get("Notes", "")
            new_notes = f"{existing_notes}\n{note}".strip()
            # display(), not the bare canonical: the sheet keeps one spelling
            # per status, and writing "rejected" here beside "Rejected" from
            # every other writer is what made the column inconsistent.
            sheets.update_row(row["row_number"],
                              {"Status": status_mod.display(new_status),
                               "Notes": new_notes})
            claims[msg["id"]] = row["row_number"]
            applied_rows.remove(row)  # don't match this row again this run
            updated.append((row, msg, new_status, evidence))
        elif len(matches) > 1:
            ambiguous.append((matches, msg))
        else:
            unmatched.append(msg)

    if not dry_run:
        _save_claims(claims)

    if overruled:
        print(f"{len(overruled)} row(s) left alone — you already corrected them:")
        for row, value in overruled:
            print(f"  row {row['row_number']:3d} {row['Company'][:24]:24s} "
                  f"locked at {value!r}")
        print()

    if already_claimed:
        print(f"{len(already_claimed)} email(s) skipped — already closed another "
              f"row on an earlier run:")
        for row, msg, claimed in already_claimed:
            print(f"  row {row['row_number']} ({row['Company'][:22]}) <- "
                  f"\"{msg['subject'][:52]}\" already used for row {claimed}")
        print()

    n_interview = sum(1 for u in updated if u[2] == status_mod.INTERVIEW_REJECTED)
    print(f"{'Would update' if dry_run else 'Updated'} {len(updated)} row(s) "
          f"({n_interview} after an interview):")
    for row, msg, new_status, evidence in updated:
        tag = " [INTERVIEW]" if new_status == status_mod.INTERVIEW_REJECTED else ""
        print(f"  row {row['row_number']}: {row['Position']} @ {row['Company']}"
              f" -> {new_status}{tag} <- \"{msg['subject'][:60]}\"")
        if evidence:
            print(f"        evidence: {evidence}")

    if ambiguous:
        print(f"\n{len(ambiguous)} email(s) matched multiple applied rows (skipped, review manually):")
        for matches, msg in ambiguous:
            rows_desc = ", ".join(f"row {r['row_number']}" for r in matches)
            print(f"  \"{msg['subject']}\" matched {rows_desc}")

    if not_rejections:
        print(f"\n{len(not_rejections)} email(s) matched the search but had no decisive "
              f"rejection wording in the body (ignored — most are confirmations).")

    if unmatched:
        print(f"\n{len(unmatched)} rejection email(s) didn't match any applied company (ignored):")
        for msg in unmatched:
            print(f"  \"{msg['subject']}\" from {msg['from']}")
