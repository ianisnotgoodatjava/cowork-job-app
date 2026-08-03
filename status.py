"""Canonical application statuses, and normalization of hand-typed values.

You edit the Status column by hand, so the sheet contains things like
"reject", "Rejected ", "interviewing", "interviewed but rejected". Every
part of this project reads status through `normalize()` so a manual entry
is understood the same way one written by the CLI is.

Unrecognized text is returned as-is (lowercased): it is your note, and the
formatter leaves those rows uncolored rather than guessing.
"""

import re

APPLIED = "applied"
INTERVIEWING = "interviewing"
INTERVIEW_REJECTED = "interview-rejected"
REJECTED = "rejected"
OFFER = "offer"

CANONICAL = [APPLIED, INTERVIEWING, INTERVIEW_REJECTED, REJECTED, OFFER]

# Longest/most specific first — "interviewed but rejected" must not be read
# as plain "rejected", and it contains both words.
_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("interview-rejected", "interview rejected", "interviewed but rejected",
      "interviewed and rejected", "interviewed, rejected", "rejected after interview",
      "interview reject", "post-interview rejection"), INTERVIEW_REJECTED),
    (("interviewing", "interview", "interviewed", "in interview",
      "interview stage", "phone screen", "onsite"), INTERVIEWING),
    (("offer", "offered", "accepted"), OFFER),
    (("rejected", "reject", "rejection", "declined", "denied"), REJECTED),
    (("applied", "apply", "submitted", "in progress", "pending"), APPLIED),
]


def normalize(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return ""
    for needles, canonical in _ALIASES:
        for n in needles:
            # Word-boundary, not substring: a plain `in` test read the "no"
            # inside "random note" as a rejection.
            if re.search(r"\b" + re.escape(n) + r"\b", s):
                return canonical
    return s


def is_open(raw: str) -> bool:
    """True when the application is still live — i.e. worth reaching out to."""
    return normalize(raw) in (APPLIED, INTERVIEWING)
