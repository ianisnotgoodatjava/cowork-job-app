"""Read/write helpers for the job-tracking Google Sheet."""

import os
from googleapiclient.discovery import build
from google_auth import get_credentials
from config import HEADERS

SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "Applications")

# header -> column letter, built once instead of HEADERS.index(header) (an
# O(n) scan) plus letter arithmetic on every column written.
_COL_LETTER = {h: chr(ord("A") + i) for i, h in enumerate(HEADERS)}

_svc = None


def _service():
    # Every caller in a run shares one client instead of rebuilding it (and
    # re-reading token.json) on every row.
    global _svc
    if _svc is None:
        _svc = build("sheets", "v4", credentials=get_credentials())
    return _svc


def ensure_headers():
    """Writes the header row if the tab is empty. If the tab already has
    headers (e.g. a pre-existing sheet), only appends any HEADERS entries
    that are missing, as new columns after the last existing one — never
    overwrites or reorders existing columns."""
    svc = _service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:Z1"
    ).execute()
    existing = result.get("values", [[]])[0] if result.get("values") else []

    if not existing:
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_TAB}!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()
        return

    # Only ever appends at the end, so config.HEADERS must already mirror the
    # sheet's real left-to-right order — rows are read positionally.
    missing = [h for h in HEADERS if h not in existing]
    if missing:
        start_col_index = len(existing)
        start_col_letter = chr(ord("A") + start_col_index)
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_TAB}!{start_col_letter}1",
            valueInputOption="RAW",
            body={"values": [missing]},
        ).execute()


def append_job_row(job: dict) -> int:
    """Appends a job application row. Returns the 1-indexed row number written."""
    ensure_headers()
    row = [job.get(h, "") for h in HEADERS]
    svc = _service()
    result = svc.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()
    updated_range = result["updates"]["updatedRange"]
    row_number = int(updated_range.split("!")[1].split(":")[0][1:])
    return row_number


def update_row(row_number: int, updates: dict):
    """Updates specific columns (by header name) on an existing row, in one
    batched write rather than one request per column.

    Columns the sheet no longer has are dropped rather than raised on: the
    tracker was trimmed to A-J, and a scanner that still passes "Notes" should
    record the status it came for instead of failing the whole write.
    """
    data = [
        {"range": f"{SHEET_TAB}!{_COL_LETTER[header]}{row_number}",
         "values": [[value]]}
        for header, value in updates.items() if header in _COL_LETTER
    ]
    if not data:
        return
    svc = _service()
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "RAW", "data": data},
    ).execute()


def list_rows() -> list[dict]:
    """Returns all logged applications as a list of dicts, in sheet order."""
    svc = _service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A2:Z"
    ).execute()
    rows = []
    for i, values in enumerate(result.get("values", []), start=2):
        padded = values + [""] * (len(HEADERS) - len(values))
        rows.append({"row_number": i, **dict(zip(HEADERS, padded))})
    return rows


def get_row(row_number: int) -> dict:
    """Return a single row by its 1-indexed sheet row number, without
    downloading the whole sheet to find it."""
    svc = _service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A{row_number}:Z{row_number}"
    ).execute()
    values = result.get("values") or [[]]
    if not values[0]:
        raise ValueError(f"No application on row {row_number}")
    padded = values[0] + [""] * (len(HEADERS) - len(values[0]))
    return {"row_number": row_number, **dict(zip(HEADERS, padded))}


def _tab_id(svc) -> int:
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == SHEET_TAB:
            return s["properties"]["sheetId"]
    raise ValueError(f"Tab not found: {SHEET_TAB}")


def delete_rows(row_numbers: list[int]) -> int:
    """Deletes the given 1-indexed sheet rows. Returns how many were removed.

    Deletes highest-numbered first, so each deletion cannot shift the rows
    still queued behind it — the row numbers passed in all refer to the same
    snapshot, and renumbering mid-loop would silently delete the wrong rows.
    """
    targets = sorted({n for n in row_numbers if n and n > 1}, reverse=True)
    if not targets:
        return 0

    svc = _service()
    tab_id = _tab_id(svc)
    requests = [
        {"deleteDimension": {"range": {
            "sheetId": tab_id, "dimension": "ROWS",
            "startIndex": n - 1, "endIndex": n,
        }}}
        for n in targets
    ]
    svc.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID, body={"requests": requests}
    ).execute()
    return len(targets)


def sort_by_applied_date() -> None:
    """Rewrites all data rows ordered by applied date, oldest first.

    Sorts in Python rather than via the Sheets sort API because the date
    column is free-text with mixed formats ('6/18', '6/18/2026') that a
    lexicographic sheet sort would order wrongly.
    """
    from date_utils import parse_date
    import datetime

    rows = list_rows()
    if not rows:
        return

    def key(row):
        d = parse_date(row.get("Applied Draes", ""))
        # Undated rows sink to the bottom rather than silently leading the sheet.
        return d or datetime.date.max

    ordered = sorted(rows, key=key)
    values = [[r.get(h, "") for h in HEADERS] for r in ordered]

    svc = _service()
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!A2",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()
