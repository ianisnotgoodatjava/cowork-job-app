"""Google Calendar as proof that a conversation actually happened.

Email wording is inference; a calendar event is a record. Every call and chat
in this job search was put on the calendar, so an event whose attendees are
@flagstar.com addresses settles both questions the scanners care about: you
networked with them, and you spoke with them before any rejection arrived.

Read-only. Nothing here writes to the calendar.
"""

import datetime
import re

from googleapiclient.discovery import build

from google_auth import get_credentials, has_calendar_access
from company_match import company_mentioned, normalize as normalize_company

# Personal/holiday noise that would otherwise match short company names.
_SKIP_SUMMARY_RE = re.compile(
    r"\b(birthday|holiday|dentist|doctor|gym|lunch break|ooo|out of office|vacation)\b",
    re.I,
)

# Generic mail hosts — an attendee at gmail.com tells us nothing about which
# company the meeting was with.
_GENERIC_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "aol.com", "me.com", "nyu.edu", "proton.me", "protonmail.com",
    "resource.calendar.google.com", "group.calendar.google.com",
}

_MEETING_HINT_RE = re.compile(
    r"\b(interview|call|chat|screen|screening|coffee|meeting|zoom|teams|"
    r"phone|connect|conversation|intro)\b",
    re.I,
)


_svc = None


def _service():
    global _svc
    if not has_calendar_access():
        raise RuntimeError(
            "calendar access not granted yet — run: "
            "./venv/bin/python cli.py enable-calendar"
        )
    if _svc is None:
        _svc = build("calendar", "v3", credentials=get_credentials())
    return _svc


def list_events(days_back: int = 460, calendar_id: str = "primary") -> list[dict]:
    """Returns past events in the window, newest first.

    Only events you actually attended matter, so declined and cancelled ones
    are dropped.
    """
    svc = _service()
    now = datetime.datetime.now(datetime.timezone.utc)
    time_min = (now - datetime.timedelta(days=days_back)).isoformat()
    time_max = now.isoformat()

    events, page = [], None
    while True:
        resp = svc.events().list(
            calendarId=calendar_id, timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy="startTime", maxResults=2500,
            pageToken=page, showDeleted=False,
        ).execute()

        for e in resp.get("items", []):
            if e.get("status") == "cancelled":
                continue
            # `self` is you; a meeting you declined is not evidence.
            declined = any(
                a.get("self") and a.get("responseStatus") == "declined"
                for a in e.get("attendees", []) or []
            )
            if declined:
                continue
            start = e.get("start", {})
            events.append({
                "id": e.get("id", ""),
                "summary": e.get("summary", "") or "",
                "description": e.get("description", "") or "",
                "location": e.get("location", "") or "",
                "start": start.get("dateTime") or start.get("date") or "",
                "organizer": (e.get("organizer") or {}).get("email", ""),
                "attendees": [a.get("email", "") for a in (e.get("attendees") or [])],
            })

        page = resp.get("nextPageToken")
        if not page:
            break

    events.reverse()
    return events


def event_date(event: dict) -> datetime.date | None:
    raw = (event.get("start") or "")[:10]
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


_domains_cache: dict[str, set[str]] = {}


def _domains(event: dict) -> set[str]:
    # matches_company() is called once per (row, event) pair — company_meetings()
    # loops over every event for every row in the sheet — but an event's own
    # attendee domains never depend on which row is asking. Cached by event id
    # so the extraction happens once per event, not once per (row, event) pair.
    key = event.get("id", "")
    if key and key in _domains_cache:
        return _domains_cache[key]

    emails = list(event.get("attendees") or [])
    if event.get("organizer"):
        emails.append(event["organizer"])
    out = set()
    for e in emails:
        if "@" in e:
            domain = e.rsplit("@", 1)[1].lower()
            if domain not in _GENERIC_DOMAINS:
                out.add(domain)

    if key:
        _domains_cache[key] = out
    return out


def matches_company(event: dict, company: str) -> str | None:
    """Evidence string when this event is clearly with `company`, else None.

    Attendee domain is checked first and trusted on its own; a title match
    additionally requires the event to look like a meeting, so a personal
    reminder that happens to contain the word never counts.
    """
    slug = normalize_company(company).replace(" ", "")
    if len(slug) >= 3:
        for domain in _domains(event):
            if slug in domain.replace("-", "").replace(".", ""):
                return f"calendar attendee @{domain}"

    haystack = f"{event['summary']} {event['location']}".lower()
    if _SKIP_SUMMARY_RE.search(haystack):
        return None
    if company_mentioned(company, haystack) and _MEETING_HINT_RE.search(haystack):
        return f"calendar event \"{event['summary'][:50]}\""
    return None


def company_meetings(company: str, events: list[dict]) -> list[tuple[dict, str]]:
    """All events that are evidence of a real conversation with `company`."""
    found = []
    for e in events:
        ev = matches_company(e, company)
        if ev:
            found.append((e, ev))
    return found
