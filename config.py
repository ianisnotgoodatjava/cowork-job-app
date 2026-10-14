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
    # .env is copied from .env.example, so these arrive pre-filled with
    # placeholders. Taking them literally would override a correct profile
    # with "you@example.com" and quietly recognise nobody's mail as yours.
    def _real(value: str) -> str:
        placeholder = ("example.com", "your name", "your_", "changeme")
        low = (value or "").strip().lower()
        return "" if not low or any(p in low for p in placeholder) else value

    email = _real(os.environ.get("OWN_EMAIL", "")) or email
    name = _real(os.environ.get("OWN_NAME", "")) or name

    alts = [re.escape(e.strip()) for e in email.split(",") if e.strip()]
    if name.strip():
        # "Ian Liu" also has to match "ianliu" and "ian.liu".
        parts = [re.escape(p) for p in name.split()]
        alts.append(r"\.?".join(parts))
    if not alts:
        # Not fatal, deliberately. This is evaluated on `import config`, which
        # happens on the way into `cli.py setup` -- the command that exists to
        # help you configure this. Raising here would make the fix unreachable
        # from the tool that explains it. `(?!)` matches nothing, so until an
        # identity is set no message is treated as yours, and setup says so.
        import sys
        print("warning: no identity configured, so no message can be "
              "recognised as yours. Set profile.email in resume_bank.json "
              "or OWN_EMAIL in .env.", file=sys.stderr)
        return r"(?!)"
    return "|".join(alts)


# Recognises your own messages, so a reply from you in a thread is never read
# as a reply from the company.
ME_RE = re.compile(_me_pattern(), re.I)

# The sheet this was built against predates the project and carries two
# spellings nothing should have to reproduce: a typo in the date column and a
# lowercase "location". HEADERS stays in that order because rows are read
# positionally, but a sheet created from scratch gets CANONICAL instead, and
# sheets.list_rows exposes both spellings so every caller works either way.
RENAMED = {
    "Applied Draes": "Applied Date",
    "location": "Location",
}

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

# What a fresh sheet gets: same order, better names.
CANONICAL = [RENAMED.get(h, h) for h in HEADERS]

# Prefilter for "does this sent email look like cold networking outreach" —
# shared by the outreach corpus builder and the scanners that find warm leads
# (reoutreach.py) and never-answered outreach (follow_up.py). OR-only, so one
# combined list only ever widens the match for every caller, never narrows it.
NETWORKING_KEYWORDS = (
    '("reaching out" OR "quick question" OR "informational" OR "coffee chat" '
    'OR "would love to learn" OR "connect" OR "your experience" OR "quick call" '
    'OR "picking your brain" OR "inquiring about" OR referral)'
)
