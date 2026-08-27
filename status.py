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
# Applied, never heard back, and old enough that it is not coming. Kept
# separate from "rejected": nobody turned you down, so it says nothing about
# how the application was received — only that the sheet should stop counting
# it as live.
NO_RESPONSE = "no response"

CANONICAL = [APPLIED, INTERVIEWING, INTERVIEW_REJECTED, REJECTED, OFFER,
             NO_RESPONSE]

# Longest/most specific first — "interviewed but rejected" must not be read
# as plain "rejected", and it contains both words.
_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("interview-rejected", "interview rejected", "interviewed but rejected",
      "interviewed and rejected", "interviewed, rejected", "rejected after interview",
      "interview reject", "post-interview rejection"), INTERVIEW_REJECTED),
    (("interviewing", "interview", "interviewed", "in interview",
      "interview stage", "phone screen", "onsite"), INTERVIEWING),
    (("offer", "offered", "accepted"), OFFER),
    # Before "rejected", because "no response" contains neither word but
    # "closed - no response" would otherwise fall through to the raw text.
    (("no response", "no reply", "never heard back", "ghosted",
      "no answer"), NO_RESPONSE),
    (("rejected", "reject", "rejection", "declined", "denied"), REJECTED),
    (("applied", "apply", "submitted", "in progress", "pending"), APPLIED),
]


# How a canonical status is spelled when written back to the sheet. The sheet
# had "Applied" from one writer and "applied" from another, plus "Rejected"
# and "rejected", because each caller hardcoded its own literal. Write through
# `display()` and that cannot happen again; `normalize()` still reads anything.
DISPLAY = {
    APPLIED: "Applied",
    INTERVIEWING: "Interviewing",
    INTERVIEW_REJECTED: "Interview-Rejected",
    REJECTED: "Rejected",
    OFFER: "Offer",
    NO_RESPONSE: "No Response",
}


def display(raw: str) -> str:
    """The spelling to write to the sheet. Unrecognized text is left alone."""
    canonical = normalize(raw)
    return DISPLAY.get(canonical, (raw or "").strip())


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
