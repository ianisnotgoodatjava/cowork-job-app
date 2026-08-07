"""Column schema for the tracking sheet.

Columns A-I already existed in your sheet before this project and are
matched exactly (order + spelling, including the "Applied Draes" typo and
lowercase "location") so nothing gets misaligned. Columns J onward are new,
appended by this project — never inserted mid-sheet, to avoid shifting your
existing data.
"""

import re

# Recognises your own messages, so a reply from you in a thread is never read
# as a reply from the company.
ME_RE = re.compile(r"icl2016@nyu\.edu|ian\.?liu", re.I)

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
