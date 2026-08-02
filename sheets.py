"""Read/write helpers for the job-tracking Google Sheet."""

import os
from googleapiclient.discovery import build
from google_auth import get_credentials
from config import HEADERS

SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "Applications")


def _service():
    return build("sheets", "v4", credentials=get_credentials())


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
    """Updates specific columns (by header name) on an existing row."""
    svc = _service()
    for header, value in updates.items():
        if header not in HEADERS:
            raise ValueError(f"Unknown column: {header}")
        col_index = HEADERS.index(header)
        col_letter = chr(ord("A") + col_index)
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_TAB}!{col_letter}{row_number}",
            valueInputOption="RAW",
            body={"values": [[value]]},
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
    """Return a single row by its 1-indexed sheet row number."""
    for row in list_rows():
        if row["row_number"] == row_number:
            return row
    raise ValueError(f"No application on row {row_number}")


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
