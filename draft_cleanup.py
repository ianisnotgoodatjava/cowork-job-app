"""Deletes outreach drafts for applications that have since been rejected.

There is no point networking into a role that is already closed, and a
stale draft sitting in Gmail is easy to send by accident.

Only unsent drafts are touched — anything already sent lives in Sent, not
Drafts, so it is out of reach here by construction.
"""

import re

import sheets
import status as status_mod
from googleapiclient.discovery import build
from google_auth import get_credentials

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _svc():
    return build("gmail", "v1", credentials=get_credentials())


def _draft_index(svc) -> dict[str, list[tuple[str, str]]]:
    """Maps recipient address -> [(draft_id, subject)]."""
    index: dict[str, list[tuple[str, str]]] = {}
    page = None
    while True:
        resp = svc.users().drafts().list(
            userId="me", maxResults=100, pageToken=page
        ).execute()
        for d in resp.get("drafts", []):
            full = svc.users().drafts().get(
                userId="me", id=d["id"], format="metadata"
            ).execute()
            headers = {
                h["name"]: h["value"]
                for h in full["message"]["payload"].get("headers", [])
            }
            m = _EMAIL_RE.search(headers.get("To", ""))
            if m:
                index.setdefault(m.group(0).lower(), []).append(
                    (d["id"], headers.get("Subject", ""))
                )
        page = resp.get("nextPageToken")
        if not page:
            break
    return index


def run(dry_run: bool = False) -> None:
    rejected = [
        r for r in sheets.list_rows()
        if status_mod.normalize(r.get("Status", "")) in (status_mod.REJECTED, status_mod.INTERVIEW_REJECTED)
        and r.get("Outreach Email Status", "").strip().lower() == "drafted"
    ]
    if not rejected:
        print("No drafted outreach on rejected rows.")
        return

    svc = _svc()
    index = _draft_index(svc)
    print(f"{len(rejected)} rejected row(s) with a drafted outreach email.\n")

    removed = 0
    for row in rejected:
        # The sheet stores the contact, not the address the draft went to, so
        # match on the address recorded in Notes if present, else by company
        # appearing in the draft subject.
        target = None
        for addr, entries in index.items():
            for draft_id, subject in entries:
                if row["Company"].split()[0].lower() in subject.lower():
                    target = (addr, draft_id, subject)
                    break
            if target:
                break

        if not target:
            print(f"  row {row['row_number']}: {row['Company']} — no matching draft found")
            continue

        addr, draft_id, subject = target
        if dry_run:
            print(f"  would delete -> {addr} | {subject[:60]}")
        else:
            svc.users().drafts().delete(userId="me", id=draft_id).execute()
            sheets.update_row(row["row_number"], {"Outreach Email Status": "cancelled (rejected)"})
            print(f"  deleted -> {addr} | {subject[:60]}")
        index.pop(addr, None)
        removed += 1

    print(f"\n{'Would delete' if dry_run else 'Deleted'} {removed} draft(s).")
