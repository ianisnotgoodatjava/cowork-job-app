"""Finds outreach emails that never got a reply.

Read-only: reports, never writes to the sheet or Gmail. Replies are detected
by Gmail thread — if a thread you started has a message from someone else,
it got a reply. That is more reliable than matching subjects, which change
("Re:", forwards) and collide across companies.
"""

import datetime
import re
from email.utils import parsedate_to_datetime

import gmail_client
from googleapiclient.discovery import build
from google_auth import get_credentials

from config import ME_RE as _ME, NETWORKING_KEYWORDS


def _svc():
    return build("gmail", "v1", credentials=get_credentials())


def _thread_has_reply(svc, thread_id: str) -> bool:
    """True when someone other than you posted in the thread."""
    t = svc.users().threads().get(
        userId="me", id=thread_id, format="metadata",
        metadataHeaders=["From"],
    ).execute()
    for m in t.get("messages", []):
        frm = next((h["value"] for h in m["payload"].get("headers", [])
                    if h["name"] == "From"), "")
        if frm and not _ME.search(frm):
            return True
    return False


def scan(days_back: int = 180, min_age_days: int = 7, limit: int = 400) -> None:
    query = f"in:sent newer_than:{days_back}d {NETWORKING_KEYWORDS}"
    print(f"Searching Gmail: {query}\n")
    msgs = gmail_client.search_messages(query, max_results=limit)

    svc = _svc()
    today = datetime.date.today()
    silent, checked = [], set()

    for m in msgs:
        # One thread can hold several of your sends; judge the thread once.
        tid = m.get("threadId") or m["id"]
        if tid in checked:
            continue
        checked.add(tid)

        try:
            sent = parsedate_to_datetime(m["date"]).date()
        except Exception:
            continue
        age = (today - sent).days
        if age < min_age_days:
            continue

        if not _thread_has_reply(svc, tid):
            silent.append((age, sent, m))

    silent.sort(key=lambda x: -x[0])
    print(f"{len(checked)} outreach thread(s) checked, "
          f"{len(silent)} with no reply after {min_age_days}+ days.\n")
    for age, sent, m in silent:
        to = m.get("to", "")[:38]
        subj = re.sub(r"^Recent NYU Graduate Inquiring [Aa]bout ", "", m["subject"])
        print(f"  {age:3d}d | {sent} | {to:38s} | {subj[:52]}")

    if silent:
        print(f"\n{len(silent)} contact(s) worth a follow-up. To draft one:")
        print("  python cli.py auto-draft-outreach --row <N> --to <email> --follow-up")
