"""Who the outreach is from — kept in a file that git does not track.

The drafting modules used to hard-code one person's name, school, degree,
employer and address directly into their templates and prompts. That works
fine privately and makes the repository unpublishable: the code cannot be
shared or reused without also publishing a CV.

So the identity lives in `profile.json`, which is gitignored, and
`profile.example.json` ships with placeholders. Anything missing falls back to
a neutral placeholder rather than to someone's real details, so a checkout
with no profile produces obviously-unsent template text instead of silently
writing mail as the previous owner.
"""

import json
import os

PROFILE_FILE = os.environ.get("OUTREACH_PROFILE", "profile.json")
EXAMPLE_FILE = "profile.example.json"

# Deliberately placeholder-shaped. If one of these turns up in a draft it is
# obvious at a glance that the profile was never filled in.
DEFAULTS = {
    "name": "YOUR NAME",
    "email": "you@example.com",
    "phone": "",
    "school": "YOUR UNIVERSITY",
    "school_short": "YOUR UNIVERSITY",
    "degree": "YOUR DEGREE",
    "graduated": "recently",
    "employer": "YOUR EMPLOYER",
    "sign_off": "Best regards",
    "subject_prefix": "Recent {school_short} Graduate Inquiring about",
    "resume_path": "",
}

_cache = None


def load(path: str | None = None) -> dict:
    """The sender's details, with placeholders for anything unset."""
    global _cache
    if _cache is not None and path is None:
        return _cache
    merged = dict(DEFAULTS)
    for candidate in ([path] if path else [PROFILE_FILE, EXAMPLE_FILE]):
        if candidate and os.path.exists(candidate):
            try:
                with open(candidate) as f:
                    merged.update({k: v for k, v in json.load(f).items()
                                   if v not in (None, "")})
                break
            except (OSError, ValueError):
                continue
    if path is None:
        _cache = merged
    return merged


def get(field: str, default: str = "") -> str:
    return str(load().get(field, default) or default)


def is_configured() -> bool:
    """False when the profile is still placeholders.

    Worth checking before anything is sent or drafted in bulk: the failure is
    silent otherwise, and produces mail addressed from "YOUR NAME".
    """
    return load().get("name") not in (None, "", DEFAULTS["name"])


def subject_prefix() -> str:
    p = load()
    return str(p["subject_prefix"]).format(
        school_short=p["school_short"], school=p["school"])


def intro_opening() -> str:
    """Just the self-introduction, for templates that add their own reason."""
    p = load()
    return (f"I hope you're doing well. My name is {p['name']}. I "
            f"{p['graduated']} graduated from {p['school_short']} with a "
            f"degree in {p['degree']} and currently work at {p['employer']}.")


def intro_sentence(area: str, company: str, focus: str = "") -> str:
    """The opening paragraph, built from the profile rather than hard-coded.

    The shape is measured from the most recent sends (UHY 11/12/2026,
    Bailard 11/5/2026) rather than invented: a one-clause introduction, then
    the specific thing of the reader's he came across, then the area he wants
    to hear about. `focus` is that specific thing -- "your background and
    leadership of UHY's Complex Financial Instruments practice" -- and falls
    back to the generic "your work at X" that 11 of the 50 corpus emails use.

    "recently" becomes "recent" so the clause reads as the noun phrase he
    actually writes: "a recent NYU graduate in Mathematics and Economics".
    """
    p = load()
    graduated = str(p["graduated"])
    adj = graduated[:-2] if graduated.endswith("ly") else graduated
    came_across = focus.strip() or f"your work at {company}"
    return (
        f"I hope you're doing well. My name is {p['name']}, a {adj} "
        f"{p['school_short']} graduate in {p['degree']}, currently working at "
        f"{p['employer']}. I came across {came_across} and would love to learn "
        f"more about your work and experience in {area}."
    )


def sign_off() -> str:
    return f"{load()['sign_off']},\n{load()['name']}"


def contact_line() -> str:
    p = load()
    return " | ".join(x for x in (p.get("email"), p.get("phone")) if x)
