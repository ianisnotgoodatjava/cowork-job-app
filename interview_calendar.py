"""Turns an agreed time in an email thread into a calendar event.

The signal is a two-part exchange: the other person proposes a time, and you
accept it. Either half on its own is not a booking -- "are you free Friday at
4?" with no reply is a question, and "that works for me" with no proposal
anywhere in the thread has no time to write down -- so both are required
before an event is created.

Nothing here emails anybody. The event is created on your own calendar with no
attendees, so Google has nobody to notify; adding the other person and pressing
send is left to you. That is deliberate — an invitation is a message to someone
outside, and this runs unattended.
"""

import datetime
import re
import zoneinfo

from googleapiclient.discovery import build

from config import ME_RE
from google_auth import get_credentials

CALENDAR_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"

EASTERN = zoneinfo.ZoneInfo("America/New_York")
DEFAULT_MINUTES = 30

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

# "Friday at 4PM EST", "tomorrow at 10:30am", "Aug 7 at 3 pm", "3:00 PM EST today"
_TIME_RE = re.compile(
    r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)"
    r"(?:\s*(?P<tz>E[SD]T|C[SD]T|M[SD]T|P[SD]T|ET|CT|MT|PT))?", re.I)
_DAY_RE = re.compile(
    r"\b(?P<rel>today|tomorrow)\b|"
    r"\b(?:on\s+)?(?P<wd>monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thurs|fri|sat|sun)\b|"
    r"\b(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+"
    r"(?P<dom>\d{1,2})\b", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# A proposal invites a yes/no; an acceptance answers one.
_PROPOSE_RE = re.compile(
    r"(how does .{0,40}\bsound\b|does .{0,40}\bwork\b|are you (free|available)|"
    r"i'?m available|i am available|would .{0,30}\bwork for you\b|"
    r"can you do|let'?s (do|say|meet)|shall we say|proposing|"
    r"i have (?:you )?down for)", re.I)
_ACCEPT_RE = re.compile(
    r"(works (perfectly|great|well|fine)?\s*for me|that works|sounds (great|good|perfect)|"
    r"i'?ll take|see you (then|on|at)|confirm(ed|ing)?\b|looking forward to it|"
    r"perfect,? (thank|see)|yes,? that|happy to do)", re.I)
_CANCEL_RE = re.compile(
    r"(cancel|reschedul|postpon|won'?t be able to make|can'?t make it|"
    r"need to move|something came up)", re.I)


def _strip_quoted(body: str) -> str:
    """Just this message, without the thread quoted underneath it."""
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", body or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\xa0]+", " ", text.replace("\r", "\n"))
    cut = re.search(r"\n\s*(?:On .{0,90}wrote:|From:\s|-{3,}\s*Original|_{5,})", text)
    return (text[:cut.start()] if cut else text).strip()


def _resolve_day(text: str, sent: datetime.date) -> datetime.date | None:
    m = _DAY_RE.search(text)
    if not m:
        return None
    if m.group("rel"):
        return sent + datetime.timedelta(days=1 if m.group("rel").lower() == "tomorrow" else 0)
    if m.group("wd"):
        target = _WEEKDAYS[m.group("wd").lower()]
        # "Friday" said on a Tuesday means this coming Friday, never the one gone.
        ahead = (target - sent.weekday()) % 7
        return sent + datetime.timedelta(days=ahead or 7)
    month = _MONTHS[m.group("mon").lower()[:3]]
    day = int(m.group("dom"))
    year = sent.year + (1 if month < sent.month - 6 else 0)
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def _resolve_time(text: str) -> tuple[int, int] | None:
    m = _TIME_RE.search(text)
    if not m:
        return None
    hour = int(m.group("hour"))
    minute = int(m.group("minute") or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    pm = m.group("ampm").lower().startswith("p")
    if pm and hour != 12:
        hour += 12
    if not pm and hour == 12:
        hour = 0
    return hour, minute


def find_agreed_time(messages: list[dict]) -> dict | None:
    """The time both sides settled on, or None.

    Walks the thread newest-first and takes the most recent proposal that you
    then accepted, so a rescheduled call books the new slot rather than the one
    it replaced.
    """
    from email.utils import parsedate_to_datetime

    parsed = []
    for m in messages:
        try:
            sent_at = parsedate_to_datetime(m["date"])
        except Exception:
            continue
        parsed.append((
            m,
            _strip_quoted(m.get("body") or m.get("snippet") or ""),
            sent_at,
            bool(ME_RE.search(m.get("from", ""))),
        ))
    # Ordered on the parsed instant, not the header string: a reply written at
    # "12:52 -0400" is later than a proposal at "16:44 +0000", but sorts before
    # it as text, which hides the acceptance from the proposal it answers.
    parsed.sort(key=lambda p: p[2])

    for i in range(len(parsed) - 1, -1, -1):
        msg, text, sent_at, from_me = parsed[i]
        if from_me or not _PROPOSE_RE.search(text):
            continue
        when = _resolve_time(text)
        if not when:
            continue
        sent = sent_at.astimezone(EASTERN).date()
        day = _resolve_day(text, sent) or sent
        # Accepted by you in a later message, and not undone after that.
        for later_msg, later_text, _, later_from_me in parsed[i + 1:]:
            if later_from_me and _ACCEPT_RE.search(later_text):
                if any(_CANCEL_RE.search(t) for _, t, _, _ in parsed[i + 1:]):
                    break
                start = datetime.datetime.combine(
                    day, datetime.time(when[0], when[1]), tzinfo=EASTERN)
                return {
                    "start": start,
                    "end": start + datetime.timedelta(minutes=DEFAULT_MINUTES),
                    "proposed_by": msg.get("from", ""),
                    "proposal": text[:200],
                    "accepted": later_text[:200],
                    "subject": msg.get("subject", ""),
                }
        continue
    return None


def create_event(agreed: dict, summary: str, contact: str = "",
                 description: str = "") -> str:
    """Drafts the event on your own calendar and returns its link.

    The other person is written into the description, not the attendee list:
    an attendee would make Google send them an invitation the moment this runs.
    Adding them and hitting Send is your step.
    """
    svc = build("calendar", "v3", credentials=get_credentials(
        extra_scopes=[CALENDAR_WRITE_SCOPE]))
    notes = description
    if contact:
        notes = f"{notes}\n\nWith: {contact}".strip()
    created = svc.events().insert(
        calendarId="primary",
        body={
            "summary": summary,
            "description": notes,
            "start": {"dateTime": agreed["start"].isoformat(), "timeZone": str(EASTERN)},
            "end": {"dateTime": agreed["end"].isoformat(), "timeZone": str(EASTERN)},
        },
        sendUpdates="none",
    ).execute()
    return created.get("htmlLink", "")
