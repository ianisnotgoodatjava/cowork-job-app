"""Finds job applications in Gmail that aren't in the sheet yet and adds them.

Company/role are parsed from the confirmation email's sender and subject.
Rows that can't be parsed confidently are reported rather than guessed at —
a wrong row is worse than a missing one, since you'd have to find and fix it.
"""

import os
import re
from email.utils import parsedate_to_datetime

from googleapiclient.discovery import build

import gmail_client
import sheets
from config import HEADERS
from company_match import normalize
from google_auth import get_credentials
from link_finder import CONFIRMATION_PHRASES

# Only patterns where the captured group is unambiguously a COMPANY.
# "interest in X" is deliberately excluded: X is a role as often as a company
# ("interest in Analyst, Fixed Income Pricing"), and a role written into the
# company column is worse than no row at all.
_SUBJECT_COMPANY_RES = [
    re.compile(r"\byour application to\s+.+?\s+at\s+(.+?)\s*$", re.I),
    re.compile(r"\bapplying to(?:\s+the)?\s+(.+?)(?:\s*[!.]|\s*$)", re.I),
    re.compile(r"\bapplication (?:to|with)\s+(.+?)(?:\s*[!.]|\s*$)", re.I),
]

# A captured "company" that is really a role/requisition, e.g.
# "Analyst, Fixed Income Pricing" or "RQ094547 - Trading Assistant".
_ROLE_LIKE_RE = re.compile(
    r"^(the\s+)?(analyst|associate|trader|intern|senior|junior|vice|director|"
    r"manager|specialist|assistant|underwrit|research|trading|portfolio|"
    r"credit|equity|fixed income|sales|quantitative|investment|finance|"
    r"commercial|rq\d|jr_|r\d{4,})",
    re.I,
)
_ROLE_TAIL_RE = re.compile(r"\s+[-–]\s+(analyst|associate|trader|senior|junior).*$", re.I)
# Trailing boilerplate/requisition noise left on a captured company name,
# e.g. "iCapital - Application Received", "BNY - 69959", "First Horizon - FHNF".
_COMPANY_TAIL_RE = re.compile(
    r"\s*[-–]\s*(application received|\d+|[A-Z]{3,}\b.*)$", re.I
)
# "Risk Analyst at Fanatics" — the real company follows the final " at ".
_TRAILING_AT_RE = re.compile(r"^.*\bat\s+(.+)$", re.I)
_SUBJECT_ROLE_RES = [
    re.compile(r"\byour application to\s+(.+?)\s+at\s+.+?$", re.I),
    re.compile(r"\bapplication for\s+(.+?)(?:\s*[-–,]\s*\d+)?\s*$", re.I),
    re.compile(r"\bapplying to(?: the)?\s+(.+?)\s+(?:role|position)\b", re.I),
]

_NOISE_SUBJECT_RE = re.compile(
    r"\b(job alert|is hiring|new job|job opening|talent community|"
    r"stay in touch|survey|newsletter|digest|webinar|verify your|"
    r"confirm your identity|sign-in link|saved as a draft|incomplete)\b",
    re.I,
)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text.strip(" -–—,:;!.")


def _parse(msg: dict) -> tuple[str, str] | None:
    subject = msg.get("subject", "")
    if _NOISE_SUBJECT_RE.search(subject):
        return None

    company = role = ""
    for rx in _SUBJECT_COMPANY_RES:
        m = rx.search(subject)
        if m:
            company = _clean(m.group(1))
            break
    for rx in _SUBJECT_ROLE_RES:
        m = rx.search(subject)
        if m:
            role = _clean(m.group(1))
            break

    company = _clean(_COMPANY_TAIL_RE.sub("", _ROLE_TAIL_RE.sub("", company)))
    # A role-shaped capture may still carry the real company after " at ".
    if _ROLE_LIKE_RE.match(company):
        m = _TRAILING_AT_RE.match(company)
        if not m:
            return None
        company = _clean(m.group(1))
        if _ROLE_LIKE_RE.match(company):
            return None
    # Requisition/job-ID fragments ("10073106-WD Credit...", "2025") are never
    # company names.
    if re.match(r"^\d", company):
        return None
    if not company or len(company) < 2 or len(company) > 60:
        return None
    return company, role


def scan(days_back: int = 730, dry_run: bool = False) -> None:
    phrase_query = " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES)
    query = f"in:inbox newer_than:{days_back}d ({phrase_query}) -from:nyu.edu"
    print(f"Searching Gmail: {query}\n")

    msgs = gmail_client.search_messages(query, max_results=500)
    print(f"Found {len(msgs)} candidate confirmation emails.")

    existing = sheets.list_rows()
    existing_keys = {
        (normalize(r["Company"]), normalize(r["Position"]))
        for r in existing
    }
    existing_companies = {normalize(r["Company"]) for r in existing if r["Company"].strip()}

    new_rows = {}
    unparsed = []
    for m in msgs:
        parsed = _parse(m)
        if not parsed:
            unparsed.append(m)
            continue
        company, role = parsed
        key = (normalize(company), normalize(role))
        if key in existing_keys or key in new_rows:
            continue
        # With no role parsed there's nothing to distinguish this from an
        # application already tracked at the same company, so skip it rather
        # than create a likely duplicate under a placeholder role.
        if not role and normalize(company) in existing_companies:
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
        }

    print(f"\n{len(new_rows)} application(s) not yet in the sheet:")
    for v in sorted(new_rows.values(), key=lambda x: x["date"]):
        flag = "" if v["known_company"] else "  [new company]"
        print(f"  {v['date']:10s} | {v['company'][:35]:35s} | {v['role'][:40]}{flag}")

    if unparsed:
        print(f"\n{len(unparsed)} email(s) couldn't be parsed into company/role (skipped):")
        for m in unparsed[:15]:
            print(f"  \"{m['subject'][:80]}\"")
        if len(unparsed) > 15:
            print(f"  ... and {len(unparsed) - 15} more")

    if dry_run:
        print("\n(dry-run — nothing written)")
        return
    if not new_rows:
        return

    svc = build("sheets", "v4", credentials=get_credentials())
    values = []
    for v in new_rows.values():
        row = {
            "Company": v["company"],
            "Position": v["role"],
            "Applied Draes": v["date"],
            "Status": "Applied",
            "Notes": "Auto-added from Gmail confirmation email",
        }
        values.append([row.get(h, "") for h in HEADERS])

    result = svc.spreadsheets().values().append(
        spreadsheetId=os.environ["GOOGLE_SHEET_ID"],
        range=f"{os.environ.get('GOOGLE_SHEET_TAB', 'application')}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()
    print(f"\nAdded {result['updates']['updatedRows']} row(s) to the sheet.")
