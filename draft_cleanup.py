"""Deletes outreach drafts for companies that have since rejected you.

There is no point networking into a role that is already closed, and a
stale draft sitting in Drafts is easy to send by accident — which nearly
happened repeatedly: drafts were written to desks at Huntington, U.S.
Bank, Conning, Fanatics and UBS *after* those rejections had arrived,
because the rejection had not been detected yet and the row still read
"applied".

This deletes at company level, not row level, and only when nothing is
still live there. U.S. Bank rejected three roles while others stayed open;
deleting a draft because one role closed would have thrown away a contact
still worth writing to.

Matching is by recipient domain first, falling back to the company name in
the subject. Address domains are the reliable half — cferrara@websterbank
is unambiguous — while subjects carry the company for firms whose mail
domain does not resemble their name.

Only unsent drafts are touched; anything already sent lives in Sent and is
out of reach here by construction.
"""

import re
from email.utils import parsedate_to_datetime

import gmail_client
import sheets
import status as status_mod
from company_match import _core, normalize
from date_utils import parse_date
from link_finder import CONFIRMATION_PHRASES
from rejection_scanner import _is_real_rejection
from googleapiclient.discovery import build
from google_auth import get_credentials

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _svc():
    return build("gmail", "v1", credentials=get_credentials())


def _slug(company: str) -> str:
    return (_core(company or "").replace(" ", "")
            or normalize(company or "").replace(" ", ""))


def _drafts(svc) -> list[dict]:
    """Every draft with a recipient: {id, addr, domain, subject}."""
    out, page = [], None
    while True:
        resp = svc.users().drafts().list(
            userId="me", maxResults=100, pageToken=page).execute()
        for draft in resp.get("drafts", []):
            full = svc.users().drafts().get(
                userId="me", id=draft["id"], format="metadata").execute()
            headers = {h["name"]: h["value"]
                       for h in full["message"]["payload"].get("headers", [])}
            match = _EMAIL_RE.search(headers.get("To", ""))
            if not match:
                continue
            addr = match.group(0).lower()
            out.append({
                "id": draft["id"],
                "addr": addr,
                "domain": addr.split("@")[1].split(".")[0],
                "subject": headers.get("Subject", ""),
            })
        page = resp.get("nextPageToken")
        if not page:
            break
    return out


def _same_company(a: str, b: str) -> bool:
    """Whether two sheet spellings are the same employer.

    The sheet holds the same company under several names, because the
    scanners derive them from whatever the ATS put in the From header:
    "bancofcal" and "Banc of California" are one bank, "Scale" and "Scale
    Microgrids" one developer. Comparing slugs for equality treated each
    pair as two firms, so one spelling looked fully rejected while the
    other still had a live application — and this would have deleted the
    drafts for it.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 5 and long_.startswith(short)


def dead_companies() -> list[str]:
    """Companies that rejected you and have no open application left."""
    rejected, still_open = {}, set()
    for row in sheets.list_rows():
        company = (row.get("Company") or "").strip()
        if not company:
            continue
        slug = _slug(company)
        state = status_mod.normalize(row.get("Status", ""))
        if status_mod.is_open(row.get("Status", "")):
            still_open.add(slug)
        elif state in (status_mod.REJECTED, status_mod.INTERVIEW_REJECTED):
            rejected[slug] = company
    return [name for slug, name in rejected.items()
            if not any(_same_company(slug, live) for live in still_open)]


def _latest(messages: list[dict]):
    """Newest parseable date in a set of messages, or None."""
    import datetime as _dt
    best = None
    for msg in messages:
        try:
            when = parsedate_to_datetime(msg.get("date", ""))
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        if best is None or when > best:
            best = when
    return best


def _confirmations_by_company(days_back: int = 120) -> dict:
    """slug -> newest genuine application-confirmation date, over all mail.

    Fetched once for the whole sweep. Doing it per company meant one Gmail
    search per candidate — 132 of them on this mailbox, which turned a
    cleanup into a multi-minute crawl for an answer that comes from the same
    result set every time.
    """
    import app_scanner

    phrases = " OR ".join(f'"{ph}"' for ph in CONFIRMATION_PHRASES)
    try:
        messages = gmail_client.search_messages(
            f"newer_than:{days_back}d ({phrases})",
            include_body=True, max_results=400)
    except Exception:
        return {}

    newest: dict = {}
    for msg in messages:
        # Parsed, not name-matched. Matching the company name against the
        # subject read KBRA's daily rating alerts as applications to Kroll,
        # because KBRA is Kroll Bond Rating Agency — which would have
        # protected Kroll's drafts forever.
        parsed = app_scanner._parse(msg)
        if not parsed:
            continue
        # A rejection parses as a confirmation for the same company —
        # "Application Status with Kroll" comes from Kroll Talent Acquisition
        # and names the role — so without this the newest no reads as a newer
        # application and the company is protected forever.
        if _is_real_rejection(msg):
            continue
        when = _latest([msg])
        if when is None:
            continue
        slug = _slug(parsed[0])
        if slug and (slug not in newest or when > newest[slug]):
            newest[slug] = when
    return newest


def has_newer_application(company: str, confirmations: dict | None = None,
                          rows: list | None = None,
                          days_back: int = 120) -> bool:
    """True when Gmail holds an application this company's sheet rows predate.

    The sheet is derived data and lags reality: a confirmation only becomes a
    row once app_scanner has parsed it, and this project keeps finding
    applications the parser missed — LinkedIn Easy Apply, SuccessFactors,
    iCIMS display names. Voya was the expensive case. Its one row was a
    rejected July role, so the company read as fully closed and its draft was
    deleted; a second application had gone in on 31 August and was simply not
    on the sheet.

    The test is about staleness rather than outcome: if the mailbox shows an
    application newer than the newest one the sheet knows about, the sheet is
    behind and nothing should be deleted on it. Comparing against rejection
    dates instead was tried and fails — rejections rarely carry the
    confirmation phrases this query needs, so they never come back and every
    company looks live.
    """
    if confirmations is None:
        confirmations = _confirmations_by_company(days_back)
    want = _slug(company)

    sent = None
    for slug, when in confirmations.items():
        if _same_company(slug, want) and (sent is None or when > sent):
            sent = when
    if sent is None:
        return False

    if rows is None:
        rows = sheets.list_rows()
    applied = [parse_date(row.get("Applied Draes", ""))
               for row in rows
               if _same_company(_slug(row.get("Company") or ""), want)]
    applied = [d for d in applied if d]
    if not applied:
        return True
    return sent.date() > max(applied)


def run(dry_run: bool = False) -> None:
    dead = dead_companies()
    if not dead:
        print("No companies where every application is closed.")
        return

    # Second gate, against Gmail rather than the sheet. See
    # has_newer_application: the sheet lags, and deleting on a stale read is
    # what cost the Voya draft.
    print(f"Checking {len(dead)} closed-looking companies against Gmail "
          f"for newer applications...", flush=True)
    confirmations = _confirmations_by_company()
    rows = sheets.list_rows()
    live = [c for c in dead if has_newer_application(c, confirmations, rows)]
    if live:
        for company in sorted(live):
            print(f"  keeping {company} — a newer application exists in Gmail "
                  f"that the sheet does not have")
        dead = [c for c in dead if c not in set(live)]
    print()

    svc = _svc()
    drafts = _drafts(svc)
    print(f"{len(dead)} company/companies fully rejected; "
          f"{len(drafts)} draft(s) with a recipient.\n", flush=True)

    removed = 0
    for company in sorted(dead):
        slug = _slug(company)
        if len(slug) < 3:
            continue
        for draft in list(drafts):
            domain = draft["domain"]
            # Prefix either way: greenskiescleanenergy vs greenskies.com. The
            # length floor stops a two-letter slug claiming a whole domain.
            by_domain = domain == slug or (
                min(len(domain), len(slug)) >= 6
                and (domain.startswith(slug) or slug.startswith(domain)))
            by_subject = (len(company) >= 4
                          and company.lower() in draft["subject"].lower())
            if not (by_domain or by_subject):
                continue

            if dry_run:
                print(f"  would delete -> {draft['addr']:38s} "
                      f"({company}) {draft['subject'][:40]}")
            else:
                svc.users().drafts().delete(userId="me", id=draft["id"]).execute()
                print(f"  deleted -> {draft['addr']:38s} "
                      f"({company}) {draft['subject'][:40]}")
            drafts.remove(draft)
            removed += 1

    print(f"\n{'Would delete' if dry_run else 'Deleted'} {removed} draft(s) "
          f"to companies that already said no.")
