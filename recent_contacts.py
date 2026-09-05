"""Who you have already written to, and how recently.

Reaching the same person twice inside a year is worse than not reaching them
at all: the second email arrives with no memory of the first, and it tells the
reader this was generated rather than meant. 19 such drafts had already
accumulated -- Fitch, KBRA, ABI Research and Bright Power each had a fresh
first-touch email queued to someone written to weeks earlier.

The record is the sent corpus, which is the only honest source: it is what
actually left the mailbox, not what this tool intended to send.

Two things are deliberately not treated as duplicates. An explicit follow-up
("Re:", "Following up") is a continuation of a conversation, not a second
introduction. And mail to your own addresses is not outreach at all -- 16
drafts sit there, and a sweep that ignored the distinction would delete them.
"""

import datetime
import json
import re

CORPUS = "outreach_corpus.json"
DEFAULT_WINDOW_DAYS = 365

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_FOLLOW_UP_RE = re.compile(r"(?i)^\s*(re:|fwd:|following up)")
_FIRST_TOUCH_RE = re.compile(r"(?i)^\s*(recent\s+)?nyu graduate")

# Your own addresses. Drafts to these are notes to self, never outreach.
OWN_ADDRESSES = {"icl2016@nyu.edu", "ianliu2002@gmail.com"}


def _address(value: str) -> str:
    match = _EMAIL_RE.search(value or "")
    return match.group(0).lower() if match else ""


def emailed_since(days: int = DEFAULT_WINDOW_DAYS,
                  path: str = CORPUS) -> dict[str, datetime.date]:
    """{address: most recent date you wrote to them} inside the window."""
    try:
        with open(path) as f:
            corpus = json.load(f)
    except (OSError, ValueError):
        return {}

    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    latest: dict[str, datetime.date] = {}
    for entry in corpus:
        address = _address(entry.get("to", ""))
        if not address or address in OWN_ADDRESSES:
            continue
        try:
            when = datetime.date.fromisoformat((entry.get("date") or "")[:10])
        except ValueError:
            continue
        if when > latest.get(address, datetime.date.min):
            latest[address] = when
    return {a: d for a, d in latest.items() if d >= cutoff}


def is_duplicate(address: str, subject: str = "",
                 recent: dict | None = None,
                 days: int = DEFAULT_WINDOW_DAYS) -> datetime.date | None:
    """The date they were last written to, if this would be a repeat intro.

    None means it is safe to send: a new contact, an old one, a deliberate
    follow-up, or your own address.
    """
    address = (address or "").lower()
    if not address or address in OWN_ADDRESSES:
        return None
    if subject and _FOLLOW_UP_RE.match(subject):
        return None
    if subject and not _FIRST_TOUCH_RE.match(subject):
        return None
    recent = emailed_since(days) if recent is None else recent
    return recent.get(address)


def filter_contacts(contacts: list[dict], days: int = DEFAULT_WINDOW_DAYS
                    ) -> tuple[list[dict], list[dict]]:
    """Splits candidate contacts into (write to these, already covered).

    Called before a draft is built rather than after, so the work of writing
    one is never spent on someone who cannot usefully receive it.
    """
    recent = emailed_since(days)
    fresh, seen = [], []
    for contact in contacts:
        address = (contact.get("email") or "").lower()
        when = recent.get(address)
        if when:
            seen.append({**contact, "last_emailed": str(when)})
        else:
            fresh.append(contact)
    return fresh, seen


def sweep(dry_run: bool = True, days: int = DEFAULT_WINDOW_DAYS) -> int:
    """Deletes queued first-touch drafts to anyone written to inside the window."""
    import gmail_client

    recent = emailed_since(days)
    doomed = []
    for draft in gmail_client.draft_headers():
        address = _address(draft["to"])
        when = is_duplicate(address, draft["subject"], recent, days)
        if when:
            doomed.append((draft, address, when))

    for draft, address, when in sorted(doomed, key=lambda d: d[2], reverse=True):
        verb = "would delete" if dry_run else "deleted"
        print(f"  {verb} {address[:34]:34s} last emailed {when}")
        if not dry_run:
            gmail_client._service().users().drafts().delete(
                userId="me", id=draft["id"]).execute()

    print(f"\n{'Would remove' if dry_run else 'Removed'} {len(doomed)} "
          f"repeat-introduction draft(s) from the last {days} days.")
    return len(doomed)
