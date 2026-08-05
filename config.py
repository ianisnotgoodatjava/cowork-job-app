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

# Convenience aliases so the rest of the code can use readable names
# without caring about the real (sometimes typo'd) header text.
COMPANY = "Company"
JOB_TITLE = "Position"
LOCATION = "location"
APPLIED_DATE = "Applied Draes"
STATUS = "Status"
