"""Draft outreach emails from a sheet row — matches Ian's past networking tone."""

from __future__ import annotations

import re

# Gmail's mobile-signature placeholder. Shows up as a floating dot at the end
# of emails sent from the phone; it is invisible in the composer, so it rode
# along into the corpus silently until someone actually looked at the raw
# text.
_SIGNATURE_ARTIFACT_RE = re.compile(r"[᐀-ᙿ]+")

# Verified typos actually seen in the sent corpus while building outreach
# examples — not a general spellchecker, just the specific slips that would
# otherwise get copied into a new draft along with the wording around them.
_KNOWN_TYPOS = (
    (re.compile(r"\bapproaches heading\b", re.I), "approaches hedging"),
)


def _clean_reused_text(text: str) -> str:
    """Strips signature artifacts and known typos out of text pulled from a
    past email, and collapses whatever blank-line mess that leaves behind."""
    text = _SIGNATURE_ARTIFACT_RE.sub("", text)
    for pattern, fix in _KNOWN_TYPOS:
        text = pattern.sub(fix, text)

    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    cleaned, blank_run = [], 0
    for line in lines:
        if line.strip():
            blank_run = 0
            cleaned.append(line)
        else:
            blank_run += 1
            if blank_run <= 1:
                cleaned.append(line)
    return "\n".join(cleaned)


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


# Below this bar, a retrieved past email is a reasonable starting point even
# though the topics differ somewhat — swapping in the new company/name is
# unlikely to leave something incoherent. Below it, the topics are different
# enough that substitution risks carrying over specifics (a competitor's
# product, a niche you don't actually share) that only fit the old company.
_REFERENCE_MIN_SCORE = 0.15

# A bare "Analyst" isn't a real subject to search on — retrieval against it
# is really just a company-name search, and TF-IDF can still return a
# moderately-high score from incidental word overlap with nothing to do with
# the actual role. "Traffic New York" (an Entry Level Management Trainee
# posting) retrieved a KBRA CMBS email this way at score 0.29, comfortably
# over the threshold above, and would have gone out talking about structured
# products and commercial real estate loans for a role that has nothing to
# do with either.
_GENERIC_TITLES = {"analyst", "associate", "intern", "analyst i", "senior analyst"}


def _closest_reference(row: dict) -> dict | None:
    """Your nearest past outreach email for this row's company/area, or None
    when nothing in the corpus is close enough to safely reuse."""
    area = (row.get("Required Skills & Experience") or "").strip()
    title = (row.get("Contact Title") or "").strip()
    role = _clean_position(row)
    if not area and not title and role.lower().strip() in _GENERIC_TITLES:
        return None

    try:
        import outreach_retrieval as orx
    except ImportError:
        return None
    try:
        idx = orx.load_index()
    except FileNotFoundError:
        return None
    query = orx.build_query(
        company=row.get("Company", ""), area=area, title=title, role=role,
    )
    hits = idx.search(query, k=1)
    if not hits or hits[0][0] < _REFERENCE_MIN_SCORE:
        return None
    return hits[0][1]


def _adapt_reference(rec: dict, row: dict, contact_name: str) -> tuple[str, str] | None:
    """Reworks the wording of your closest past outreach email onto the new
    contact/company, or None if the old company's name can't be fully swapped
    out — the KBRA row once drew on a Fitch Ratings email and kept saying
    "Fitch" in the body, because the substitution only replaced the exact
    company string, not every way that company got referred to. Silently
    mailing the wrong company's name is worse than the generic template, so
    this verifies the swap actually took before trusting the result."""
    from company_match import _core

    first = _first_name(contact_name) if contact_name else "there"
    old_company = (rec.get("company") or "").strip()
    new_company = (row.get("Company") or "the firm").strip()

    body = _clean_reused_text(rec.get("body", ""))
    if old_company and new_company:
        body = re.sub(re.escape(old_company), new_company, body, flags=re.I)

    # Every word of the core phrase, not just the first or the whole phrase
    # together: _core("Fitch Ratings") is "fitch ratings", but the leaked
    # reference was the bare word "Fitch" alone, and _core("B. Riley
    # Securities") is "b riley" — "b" is too short to mean anything, but
    # "riley" leaking through is exactly the same failure. Checking each
    # word means an occasional false trigger on a generic word one company's
    # core phrase happens to share with the industry (e.g. "ratings") — that
    # just falls back to the template, which is the safe direction to fail
    # in. A wrong company's name actually reaching your draft is not.
    # Same company on both sides (a genuine repeat topic, like a second role
    # at Conning) isn't a swap at all — the brand word is supposed to still
    # be there, and flagging it would reject a reference that never needed
    # adapting in the first place.
    same_company = _core(old_company) == _core(new_company)
    old_words = [] if same_company else [w for w in _core(old_company).split() if len(w) >= 3]
    if old_words and re.search(
            r"\b(" + "|".join(re.escape(w) for w in old_words) + r")\b", body, re.I):
        return None

    body = re.sub(
        r"^(Dear|Hello|Hi)\s+[A-Za-z]+,",
        (f"\\1 {first}," if contact_name else "Hello,"),
        body, count=1,
    )

    # _subject_area(row) already appends " at {company}" itself when the
    # title doesn't already read as "X at Y" — appending it again here
    # unconditionally produced "... at Conning at Conning" whenever the
    # reference's own area was blank and this fell through to it.
    area = (rec.get("area") or "").strip() or _subject_area(row)
    if " at " in area.lower():
        subject = f"Recent NYU Graduate Inquiring about {area}"
    else:
        subject = f"Recent NYU Graduate Inquiring about {area} at {new_company}"
    return subject, body


def generate_outreach(row: dict) -> tuple[str, str]:
    """Return (subject, body) for a networking outreach email.

    Deliberately never mentions having applied. Ian's own most effective
    emails lead with interest in the person's work — saying "I applied to X"
    reads as transactional and invites a recruiting-process reply rather
    than a conversation.

    Prefers adapting your closest past outreach email (found via TF-IDF
    retrieval over the sent corpus) over the generic template below — your
    own wording, grounded in a real past email, beats boilerplate every
    time. Falls back to the template when nothing in the corpus is close
    enough to reuse without risking mismatched specifics.
    """
    contact_name = (row.get("Contact Name") or "").strip()

    reference = _closest_reference(row)
    if reference is not None:
        adapted = _adapt_reference(reference, row, contact_name)
        if adapted is not None:
            return adapted

    company = (row.get("Company") or "the firm").strip()
    contact_title = (row.get("Contact Title") or "").strip()
    first = _first_name(contact_name) if contact_name else "there"
    position = _clean_position(row)
    # The middle paragraph carries the whole email. A generic version is
    # always weaker than one written with real knowledge of the desk, so
    # prefer the sheet's captured posting detail when it is filled in.
    area = (row.get("Required Skills & Experience") or "").strip()

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
    # A bare "Analyst" is not a subject anyone can be interested in; fall
    # through to the company phrasing rather than emit "interested in analyst".
    pos = position.lower().strip()
    subject = area.lower() if area else ("" if pos in _GENERIC_TITLES else pos)
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
