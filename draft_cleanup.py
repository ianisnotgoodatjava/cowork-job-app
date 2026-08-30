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

import sheets
import status as status_mod
from company_match import _core, normalize
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


def run(dry_run: bool = False) -> None:
    dead = dead_companies()
    if not dead:
        print("No companies where every application is closed.")
        return

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
