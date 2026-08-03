"""Draft outreach emails from a sheet row — matches Ian's past networking tone."""

from __future__ import annotations

import re


def _first_name(full_name: str) -> str:
    return full_name.strip().split()[0] if full_name.strip() else "there"


def _clean_position(row: dict) -> str:
    """Some sheet rows already have 'at {Company}' baked into the Position
    text (e.g. 'Analyst (NY) at KBRA'). Strip that so it isn't duplicated
    when the template adds its own 'at {company}' phrasing."""
    position = (row.get("Position") or "").strip()
    company = (row.get("Company") or "").strip()
    if company and position.lower().endswith(f" at {company.lower()}"):
        position = position[: -(len(company) + 4)].strip()
    return position


def _subject_area(row: dict) -> str:
    title = (row.get("Contact Title") or "").strip() or _clean_position(row) or "the team"
    company = (row.get("Company") or "the company").strip()
    if title.lower().endswith(f" at {company.lower()}"):
        return title
    if " at " in title.lower():
        return title
    return f"{title} at {company}"


def _team_phrase(row: dict) -> str:
    title = (row.get("Contact Title") or "").strip()
    company = (row.get("Company") or "").strip()
    position = _clean_position(row)

    if title and company and title.lower() not in company.lower():
        if any(w in title.lower() for w in ("team", "group", "department", "division")):
            return f"{company}'s {title}"
        return f"your experience as {title} at {company}"

    if position and company:
        return f"your experience on {company}'s {position} team"

    if company:
        return f"your experience at {company}"
    return "your experience"


def _interest_paragraph(row: dict) -> str:
    company = (row.get("Company") or "the company").strip()
    position = _clean_position(row) or "this role"
    requirements = (row.get("Requirements") or "").strip()
    contact_title = (row.get("Contact Title") or "").strip()
    location = (row.get("location") or "").strip()

    opener = (
        f"I recently applied to the {position} role at {company} and am very interested "
        f"in learning more about the team and the work you do."
    )

    if requirements:
        detail = (
            f"The posting especially caught my attention because of its focus on "
            f"{requirements}. I'd love to learn more about how your team applies these "
            f"skills day to day, the types of problems you work on, and what you've found "
            f"most rewarding in your role."
        )
    elif contact_title:
        detail = (
            f"Given your role as {contact_title}, I'd love to hear about your career path, "
            f"what your day-to-day responsibilities look like, and what you've found most "
            f"rewarding about working at {company}."
        )
    else:
        detail = (
            f"I'd love to learn more about the team, the types of projects you work on, "
            f"and what you've found most rewarding about being at {company}."
        )

    if location and location.lower() not in ("remote", "n/a"):
        detail = detail.replace(
            "most rewarding in your role.",
            f"most rewarding in your role, especially given the {location} location.",
        )

    return f"{opener}\n\n{detail}"


def generate_outreach(row: dict) -> tuple[str, str]:
    """Return (subject, body) for a networking outreach email.

    Deliberately never mentions having applied. Ian's own most effective
    emails lead with interest in the person's work — saying "I applied to X"
    reads as transactional and invites a recruiting-process reply rather
    than a conversation.
    """
    company = (row.get("Company") or "the firm").strip()
    contact_name = (row.get("Contact Name") or "").strip()
    contact_title = (row.get("Contact Title") or "").strip()
    first = _first_name(contact_name) if contact_name else "there"
    position = _clean_position(row)
    # The middle paragraph carries the whole email. A generic version is
    # always weaker than one written with real knowledge of the desk, so
    # prefer the Job Description when it is filled in.
    area = (row.get("Job Description") or "").strip()

    subject = f"Recent NYU Graduate Inquiring about {_subject_area(row)}"

    if contact_title:
        hook = f"your experience {_lead_in(contact_title)} at {company}"
    else:
        hook = f"your work at {company}"

    greeting = f"Dear {first}," if contact_name else "Hello,"
    body = (
        f"{greeting}\n\n"
        "I hope you're doing well. My name is Ian Liu. I recently graduated "
        "from NYU with a degree in Mathematics and Economics and currently "
        f"work at REDACTED. I came across your profile and was particularly "
        f"interested in {hook}.\n\n"
        f"{_interest_para(area, position, company)}\n\n"
        "If you have 15-20 minutes over the next few weeks, I'd greatly "
        "appreciate the opportunity to speak with you, either over the phone "
        "or in person. I'd love to hear about your career path, what your "
        "day-to-day work looks like, and any advice you might have for "
        "someone looking to build a career in this area. I've attached my "
        "resume for your reference.\n\n"
        "Thank you for your time and consideration. I look forward to the "
        "opportunity to connect.\n\n"
        "Best regards,\n"
        "Ian Liu"
    )
    return subject, body


def _interest_para(area: str, position: str, company: str) -> str:
    subject = area.lower() if area else (position.lower() if position else "")
    if subject:
        opening = f"I've become increasingly interested in {subject} because of"
    else:
        opening = f"I've become increasingly interested in the work {company} does because of"
    return (
        f"{opening} the way it brings together analytical work and real "
        "market decisions. I'm especially curious about how your team "
        "approaches it day to day and what the work looks like up close."
    )


def _lead_in(title: str) -> str:
    """'Senior Director, ABS' -> 'in ABS'; 'VP, Head of X' -> 'leading X'."""
    t = title.strip()
    if re.search(r"\bhead of\b", t, re.I):
        return "leading " + re.sub(r".*\bhead of\b\s*", "", t, flags=re.I)
    tail = t.split(",", 1)[1].strip() if "," in t else t
    return f"in {tail}" if tail else f"as {t}"


def generate_follow_up(row: dict) -> tuple[str, str]:
    """Return (subject, body) for a polite follow-up on an earlier outreach."""
    contact_name = (row.get("Contact Name") or "").strip()
    first = _first_name(contact_name) if contact_name else "there"
    subject_area = _subject_area(row)

    subject = f"Re: Recent NYU Graduate Inquiring about {subject_area}"
    greeting = f"Dear {first}," if contact_name else "Hello,"
    body = (
        f"{greeting}\n\n"
        "I hope this message finds you well. I previously reached out to express my "
        f"interest in connecting and learning more about {subject_area}, and I "
        "completely understand that schedules can get busy. I just wanted to follow up "
        "on my earlier message in case it was overlooked.\n\n"
        "Please don't hesitate to let me know if now isn't a good time. I greatly "
        "appreciate and respect your time.\n\n"
        "Sincerely,\n"
        "Ian Liu"
    )
    return subject, body
