"""Draft outreach emails from a sheet row — matches Ian's past networking tone."""

from __future__ import annotations

import re


def _first_name(full_name: str) -> str:
    return full_name.strip().split()[0] if full_name.strip() else "there"


def _subject_area(row: dict) -> str:
    title = (row.get("Contact Title") or row.get("Position") or "the team").strip()
    company = (row.get("Company") or "the company").strip()
    if title.lower().endswith(f" at {company.lower()}"):
        return title
    if " at " in title.lower():
        return title
    return f"{title} at {company}"


def _team_phrase(row: dict) -> str:
    title = (row.get("Contact Title") or "").strip()
    company = (row.get("Company") or "").strip()
    position = (row.get("Position") or "").strip()

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
    position = (row.get("Position") or "this role").strip()
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
    """Return (subject, body) for a networking outreach email."""
    company = (row.get("Company") or "").strip()
    contact_name = (row.get("Contact Name") or "").strip()
    first = _first_name(contact_name) if contact_name else "there"
    subject_area = _subject_area(row)
    team_phrase = _team_phrase(row)

    subject = f"Recent NYU Graduate Inquiring about {subject_area}"
    if company and company not in subject:
        subject = f"{subject} at {company}"

    greeting = f"Dear {first}," if contact_name else "Hello,"
    intro = (
        f"{greeting}\n\n"
        "I hope you're doing well. My name is Ian Liu. I recently graduated from NYU "
        "with a degree in Mathematics and Economics and currently work at REDACTED. I "
        f"came across your profile and wanted to reach out to learn more about {team_phrase}."
    )
    interest = _interest_paragraph(row)
    closing = (
        "If you have 15–20 minutes available for a brief call or coffee sometime in "
        "the coming weeks, I would greatly appreciate the opportunity to learn more "
        "about your career path and experiences. I've attached my resume for your "
        "reference.\n\n"
        "Thank you for your time and consideration. I look forward to hearing from you.\n\n"
        "Sincerely,\n"
        "Ian Liu"
    )

    body = f"{intro}\n\n{interest}\n\n{closing}"
    return subject, body


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
