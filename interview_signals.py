"""Decides whether a real interview happened, from email wording and thread history.

Two questions, both answered from the same vocabulary:

  * `post_interview_evidence()` — does this rejection email show that you had
    already interviewed? If so the row is `interview-rejected` (yellow) rather
    than a plain `rejected` (red), which is a materially different outcome:
    getting to a conversation and losing there is not the same as an ATS
    filtering you out.
  * `invite_evidence()` — is this reply offering/scheduling an interview? Used
    to flip a live row to `interviewing`.

The phrase lists are taken from real mail in this account, not invented:
Wolfe Research wrote "taking the time to interview", Flagstar wrote
"I enjoyed meeting with you". Both are post-interview tells that no amount of
rejection-phrase matching would have caught.
"""

import html
import re

# Only phrases that presuppose a conversation already took place. Bare
# "interview" is deliberately absent: rejections routinely say "we will not be
# moving forward to the interview stage", which means the opposite.
POST_INTERVIEW_PHRASES = [
    # Gratitude for time actually spent — the most common tell.
    "taking the time to interview",
    "taking the time to speak with",
    "taking the time to meet",
    "taking the time to chat",
    "taking the time to talk",
    "taking the time to come in",
    "thank you for interviewing",
    "thank you for meeting with",
    "thank you for speaking with",
    "thank you for taking the time to speak",
    "thanks for taking the time to speak",
    # Warm close from a human interviewer (Flagstar's wording).
    "enjoyed meeting with you",
    "enjoyed meeting you",
    "enjoyed speaking with you",
    "enjoyed talking with you",
    "enjoyed our conversation",
    "enjoyed our discussion",
    "pleasure meeting you",
    "pleasure speaking with you",
    "pleasure to meet you",
    "it was great speaking with you",
    "it was great to meet you",
    # Explicit back-references to the interview itself.
    "after your interview",
    "following your interview",
    "after your recent interview",
    "your recent interview",
    "since your interview",
    "based on your interview",
    "during your interview",
    "after your conversation with",
    "following your conversation with",
    "after your conversations with",
    "after speaking with our team",
    "after meeting with our team",
    "after you met with",
    "you interviewed for",
    "interviewed with our",
    "interviewed for this",
    "interviewed for the",
    "we met with you",
    "we spoke with you",
    "your interview with",
    "the interview process",
    "completed the interview",
]

# An interview being offered or scheduled. Used both to read thread history
# (did an invite precede this rejection?) and to flip live rows to
# `interviewing`.
INTERVIEW_INVITE_PHRASES = [
    "phone screen",
    "screening call",
    "zoom interview",
    "video interview",
    "first round interview",
    "first-round interview",
    "initial interview",
    "schedule an interview",
    "scheduling an interview",
    "set up an interview",
    "invite you to interview",
    "invitation to interview",
    "invite you to a",
    "inviting you to a scheduled zoom meeting",
    "would like to speak with you",
    "would like to connect with you",
    "would like to schedule",
    "like to set up a call",
    "wants to set up a call",
    "set up a time to speak",
    "set up a time to connect",
    "please provide your availability",
    "let me know your availability",
    "your availability for a",
    "availability for a brief",
    "available for a brief",
    "available for a 15",
    "available for a 30",
    "learn more about your background",
    "speak with you further about your background",
    "move forward with an interview",
    "move you forward to an interview",
    "next round",
    "next steps in the process",
    "meeting invite from me",
    "i will give you a call",
    "i will coordinate a time",
    "coordinate a time for us to connect",
    # Gilmartin's AVP wrote "I'd like to schedule a brief call ... for a 15/20
    # min call and I can send an invite", and none of the above matched: the
    # list only had the formal "would like to", never the contraction, and
    # only "learn more about your background", never "learn more about you".
    "like to schedule",
    "schedule a brief call",
    "schedule a call",
    "schedule some time",
    "brief call",
    "quick call to",
    "min call",
    "minute call",
    "learn more about you",
    "send an invite",
    "sent an invite",
    "send you an invite",
    "good time windows",
    "time windows",
    "look forward to connecting",
    "share more details about the role",
    "follow-up to your application",
    "follow up to your application",
]

# Your own thank-you note is the single most reliable tell that an interview
# happened: you send one after every conversation ("Thank You - Ian Liu"), and
# it exists even when the company's rejection is terse or never arrives at all.
SELF_INTERVIEW_PHRASES = [
    "thank you for your time earlier",
    "thank you for your time today",
    "thank you for your time yesterday",
    "thank you for your time this",
    "thank you for taking the time to speak with me",
    "thank you for speaking with me",
    "thank you for the opportunity to speak",
    "thank you for the conversation",
    "it was an honor to share my experiences",
    "it was a pleasure speaking with you",
    "it was great speaking with you",
    "enjoyed our conversation",
    "enjoyed speaking with you",
    "i look forward to the next steps",
    "look forward to the upcoming steps",
    "grateful for the opportunity to speak",
    "thank you for your time and consideration following",
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Same trap the rejection scanner hits: ATS confirmations describe interviews
# hypothetically ("if your background is a match, a recruiter will reach out to
# schedule an interview"). A phrase behind one of these leads is boilerplate.
_CONDITIONAL_LEAD_RE = re.compile(
    r"(if|should|unless|whether|in case|in the event|when|once)\b[^.]{0,70}$", re.I
)

# "we are unable to offer you an interview", "will not be moving you forward to
# speak with the team" — the phrase is present but negated.
_NEGATED_LEAD_RE = re.compile(
    r"\b(not|n't|unable to|decline|regret that we (cannot|can't)|without)\b[^.]{0,45}$", re.I
)

# Machine senders can invite you to an interview, but they never prove one
# happened, and they are never a networking "connection".
_AUTOMATED_RE = re.compile(
    r"no[-_.]?reply|donotreply|do[-_.]?not[-_.]?reply|notifications?@|alert@|"
    r"mailer|automated|postmaster|bounce",
    re.I,
)

# An out-of-office autoresponder comes from the person's own mailbox (real
# address, passes _AUTOMATED_RE clean), but it is still nobody actually
# reading or answering you. Blommer's Shaurya Sarkar and 1823 Partners'
# Diana Luk each sent one of these off a cold email and neither has ever
# replied for real.
_AUTORESPONSE_SUBJECT_RE = re.compile(
    r"^(automatic reply|auto[-_.]?reply|automatic response|out of office)\b",
    re.I,
)


def plain_text(body: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", body or ""))).lower()


def _find_phrase(text: str, phrases: list[str]) -> str | None:
    """First phrase present in a non-conditional, non-negated context."""
    for phrase in phrases:
        idx = text.find(phrase)
        while idx >= 0:
            lead = text[max(0, idx - 70):idx]
            if not _CONDITIONAL_LEAD_RE.search(lead) and not _NEGATED_LEAD_RE.search(lead):
                return phrase
            idx = text.find(phrase, idx + 1)
    return None


def is_automated(from_header: str, subject: str = "") -> bool:
    return bool(_AUTOMATED_RE.search(from_header or "")
                or _AUTORESPONSE_SUBJECT_RE.search((subject or "").strip()))


def post_interview_evidence(msg: dict) -> str | None:
    """Wording in this message proving an interview already happened."""
    return _find_phrase(plain_text(msg.get("body", "")), POST_INTERVIEW_PHRASES)


def invite_evidence(msg: dict) -> str | None:
    """Wording offering or scheduling an interview.

    Automated senders are excluded: an ATS 'we may invite you to interview'
    blast is not an invitation.
    """
    if is_automated(msg.get("from", ""), msg.get("subject", "")):
        return None
    return _find_phrase(plain_text(msg.get("body", "")), INTERVIEW_INVITE_PHRASES)


# Your cold-outreach threads all carry this subject shape, and the thank-you
# note you send after a networking coffee chat looks word-for-word like the one
# you send after an interview. BMO was marked interview-rejected off a chat
# with someone who was only ever helping you look — a real conversation, worth
# Network=TRUE, but not an interview for the role.
_NETWORKING_SUBJECT_RE = re.compile(
    r"(inquiring about|informational|coffee chat|quick question|reaching out|"
    r"introduction|intro call|networking|would love to learn|picking your brain)",
    re.I,
)

def is_networking_thread(msg: dict) -> bool:
    """True when this thread is your own cold outreach, not an application."""
    return bool(_NETWORKING_SUBJECT_RE.search(msg.get("subject") or ""))


def self_interview_evidence(msg: dict) -> str | None:
    """A thank-you note you sent after interviewing for a role.

    A note in one of your outreach threads never counts, with no escape
    hatch. Allowing one for notes that mention "the role" or "next steps" was
    tried and reverted: those threads are *about* roles, so the wording shows
    up in ordinary coffee chats and it re-flagged bp and Liberty Mutual as
    interviews that never happened. Networking notes still set Network=TRUE
    via self_networking_evidence(); they just never move Status.
    """
    # The subject test below keys on "Thank you...", which is also how every
    # ATS opens its confirmation ("Thank you for applying to SMBC"). Those are
    # inbound, so without this guard an application receipt read as proof you
    # had interviewed.
    if not _is_from_me(msg) or is_networking_thread(msg):
        return None

    body = plain_text(msg.get("body", ""))
    subject = (msg.get("subject") or "").lower()
    if subject.startswith("thank you") or "thank you -" in subject:
        return f"your note \"{msg['subject'][:45]}\""
    ev = _find_phrase(body, SELF_INTERVIEW_PHRASES)
    return f"your note \"{ev}\"" if ev else None


def _is_from_me(msg: dict) -> bool:
    from config import ME_RE
    return bool(ME_RE.search(msg.get("from", "")))


def self_networking_evidence(msg: dict) -> str | None:
    """A note you sent showing a real conversation happened, interview or not.

    Used for the Network column, which asks only whether the outreach
    connected — it stays true for a coffee chat that led nowhere.
    """
    # Same trap as above: "Thank you for applying to X" is the ATS writing to
    # you, not you writing to a person.
    if not _is_from_me(msg):
        return None
    subject = (msg.get("subject") or "").lower()
    if subject.startswith("thank you") or "thank you -" in subject:
        return f"your note \"{msg['subject'][:45]}\""
    ev = _find_phrase(plain_text(msg.get("body", "")), SELF_INTERVIEW_PHRASES)
    return f"your note \"{ev}\"" if ev else None


def thread_interview_evidence(messages: list[dict], me_re: re.Pattern,
                              before_id: str | None = None) -> str | None:
    """Evidence from thread history that an interview was arranged or held.

    Your own messages are read differently from theirs: an "here is my
    availability" reply just quotes their invite and proves nothing, but a
    thank-you note proves the conversation already happened. Stops at
    `before_id` so a later message cannot justify an earlier verdict.
    """
    for m in messages:
        if before_id and m.get("id") == before_id:
            break
        if me_re.search(m.get("from", "")):
            ev = self_interview_evidence(m)
            if ev:
                return ev
            continue
        ev = invite_evidence(m)
        if ev:
            return ev
        # A calendar/Zoom invite addressed to you is proof on its own.
        subject = (m.get("subject") or "").lower()
        if "interview" in subject and not _NEGATED_LEAD_RE.search(subject):
            return f"subject: {m['subject'][:60]}"
    return None
