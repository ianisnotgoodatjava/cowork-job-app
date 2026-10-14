"""Column schema for the tracking sheet.

Columns A-I already existed in your sheet before this project and are
matched exactly (order + spelling, including the "Applied Draes" typo and
lowercase "location") so nothing gets misaligned. Columns J onward are new,
appended by this project — never inserted mid-sheet, to avoid shifting your
existing data.
"""

import re

def _me_pattern() -> str:
    """Patterns matching your own From/To headers.

    Built from your own details rather than hard-coded, so this works for
    anyone who clones the project. Read from resume_bank.json first, then
    OWN_EMAIL / OWN_NAME in .env. Matching on name as well as address
    catches mail sent from a second address.
    """
    import json
    import os

    email, name = "", ""
    try:
        with open("resume_bank.json", encoding="utf-8") as f:
            profile = json.load(f).get("profile", {})
        email, name = profile.get("email", ""), profile.get("name", "")
    except (OSError, ValueError):
        pass
    email = os.environ.get("OWN_EMAIL") or email
    name = os.environ.get("OWN_NAME") or name

    alts = [re.escape(e.strip()) for e in email.split(",") if e.strip()]
    if name.strip():
        # "Ian Liu" also has to match "ianliu" and "ian.liu".
        parts = [re.escape(p) for p in name.split()]
        alts.append(r"\.?".join(parts))
    if not alts:
        raise SystemExit(
            "Cannot tell which messages are yours. Set `profile.email` in "
            "resume_bank.json, or OWN_EMAIL in .env.")
    return "|".join(alts)


# Recognises your own messages, so a reply from you in a thread is never read
# as a reply from the company.
ME_RE = re.compile(_me_pattern(), re.I)

HEADERS = [
    "Company",
    "Position",
    "location",
    "Applied Draes",              # your existing "applied date" column
    "Job ID",
    "Network",                    # Yes/No — did you speak to someone in the role
    "Status",                     # applied / rejected / interview / offer
    "Result",
    "Job Link",                   # the posting itself (LinkedIn, Workday, ...)
    "Required Skills & Experience",
    "Posted",
    "Contact",
]

# Prefilter for "does this sent email look like cold networking outreach" —
# shared by the outreach corpus builder and the scanners that find warm leads
# (reoutreach.py) and never-answered outreach (follow_up.py). OR-only, so one
# combined list only ever widens the match for every caller, never narrows it.
NETWORKING_KEYWORDS = (
    '("reaching out" OR "quick question" OR "informational" OR "coffee chat" '
    'OR "would love to learn" OR "connect" OR "your experience" OR "quick call" '
    'OR "picking your brain" OR "inquiring about" OR referral)'
)
