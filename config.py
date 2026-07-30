"""Column schema for the tracking sheet.

Columns A-I already existed in your sheet before this project and are
matched exactly (order + spelling, including the "Applied Draes" typo and
lowercase "location") so nothing gets misaligned. Columns J onward are new,
appended by this project — never inserted mid-sheet, to avoid shifting your
existing data.
"""

HEADERS = [
    # --- pre-existing columns, do not reorder/rename ---
    "Company",
    "Position",
    "location",
    "Applied Draes",              # your existing "applied date" column
    "Job ID",
    "Network",
    "Status",                     # applied / rejected / interview / offer
    "Result",
    "Apply Via",
    # --- new columns appended by this project ---
    "Requirements",
    "Job Link",
    "Application Email Status",   # not needed / drafted / sent
    "Contact Name",
    "Contact Title",
    "Contact LinkedIn URL",
    "Outreach Email Status",      # not started / drafted / sent
    "Notes",
]

# Convenience aliases so the rest of the code can use readable names
# without caring about the real (sometimes typo'd) header text.
COMPANY = "Company"
JOB_TITLE = "Position"
LOCATION = "location"
APPLIED_DATE = "Applied Draes"
STATUS = "Status"
