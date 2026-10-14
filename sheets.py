"""Read/write helpers for the job-tracking Google Sheet."""

import os
from googleapiclient.discovery import build
from google_auth import get_credentials
from config import CANONICAL, HEADERS, RENAMED

SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "Applications")

# header -> column letter, built once instead of HEADERS.index(header) (an
# O(n) scan) plus letter arithmetic on every column written.
_COL_LETTER = {h: chr(ord("A") + i) for i, h in enumerate(HEADERS)}

_svc = None

# The sheet read back, for the life of one process. 38 call sites across the
# project ask for every row, and a pipeline run walks most of them: each was a
# fresh round trip costing about half a second, for data that does not change
# between them. Invalidated by every writer below -- a stale cache would have
# a scanner decide against rows another step had already rewritten.
_rows_cache: list | None = None


def _invalidate() -> None:
    global _rows_cache
    _rows_cache = None


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
    # Anything read back now would predate this write.
    _invalidate()
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
            body={"values": [CANONICAL]},
        ).execute()
        return

    # Only ever appends at the end, so config.HEADERS must already mirror the
    # sheet's real left-to-right order — rows are read positionally.
    missing = [h for h in HEADERS if h not in existing]
    if missing:
        start_col_index = len(existing)
        start_col_letter = chr(ord("A") + start_col_index)
        # The tab's grid is a fixed width, and writing past it fails with
        # "exceeds grid limits" rather than growing it. Widen first.
        needed = start_col_index + len(missing)
        tab_id = _tab_id(svc)
        meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
        width = next(
            (sh["properties"]["gridProperties"]["columnCount"]
             for sh in meta["sheets"]
             if sh["properties"]["sheetId"] == tab_id), needed)
        if width < needed:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=SHEET_ID,
                body={"requests": [{"appendDimension": {
                    "sheetId": tab_id, "dimension": "COLUMNS",
                    "length": needed - width,
                }}]},
            ).execute()
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_TAB}!{start_col_letter}1",
            valueInputOption="RAW",
            body={"values": [missing]},
        ).execute()


def append_job_row(job: dict) -> int:
    """Appends a job application row. Returns the 1-indexed row number written.

    Writes to an explicit row number rather than using the Sheets API's own
    `values.append` + INSERT_ROWS: that call auto-detects "the table" from the
    given range and inserts right after wherever it decides that table ends,
    which is not necessarily the sheet's actual last row. On this sheet it
    silently spliced a new row in after row 350 and pushed roughly 55 rows of
    real data down by three — the values were intact, just at the wrong row
    numbers, which is worse than losing them since nothing looked broken.
    Finding the true last row ourselves and writing there directly has no such
    ambiguity.
    """
    # Anything read back now would predate this write.
    _invalidate()
    ensure_headers()
    job = _with_aliases(dict(job))
    row = [job.get(h, "") for h in HEADERS]
    svc = _service()
    next_row = len(list_rows()) + 2  # +1 for the header, +1 to land past the last row
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!A{next_row}",
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()
    return next_row


def update_row(row_number: int, updates: dict):
    """Updates specific columns (by header name) on an existing row, in one
    batched write rather than one request per column.

    Columns the sheet no longer has are dropped rather than raised on: the
    tracker was trimmed to A-J, and a scanner that still passes "Notes" should
    record the status it came for instead of failing the whole write.
    """
    # Anything read back now would predate this write.
    _invalidate()
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


def update_rows(patches: dict[int, dict], chunk: int = 500) -> int:
    """Applies many row updates in one write instead of one call per row.

    `update_row` already batches the columns within a single row, but a
    sweep over the whole sheet still cost one HTTP round trip per row — a
    few hundred of them for a column-wide cleanup, which is slow enough to
    hit rate limits. The Sheets API takes ranges from any number of rows in
    a single batchUpdate, so the whole sweep becomes one call.

    Returns the number of cell ranges written.
    """
    # Anything read back now would predate this write.
    _invalidate()
    data = [
        {"range": f"{SHEET_TAB}!{_COL_LETTER[header]}{row_number}",
         "values": [[value]]}
        for row_number, updates in patches.items()
        for header, value in updates.items() if header in _COL_LETTER
    ]
    if not data:
        return 0
    svc = _service()
    # Chunked so one enormous sweep cannot exceed the request size limit.
    for i in range(0, len(data), chunk):
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"valueInputOption": "RAW", "data": data[i:i + chunk]},
        ).execute()
    return len(data)


def _with_aliases(row: dict) -> dict:
    """Expose both spellings of every renamed column.

    Rows are read positionally, so the dict is keyed by whatever HEADERS
    calls a column. Callers written against either spelling then work
    against either sheet -- the original one with "Applied Draes" in it, and
    a sheet created today with "Applied Date".
    """
    for legacy, canonical in RENAMED.items():
        if legacy in row and canonical not in row:
            row[canonical] = row[legacy]
        elif canonical in row and legacy not in row:
            row[legacy] = row[canonical]
    return row


def list_rows(refresh: bool = False) -> list[dict]:
    """Returns all logged applications as a list of dicts, in sheet order.

    Served from the per-process cache unless `refresh`, or unless something
    has been written since. Callers get a copy: several of them annotate the
    dicts they are handed, and handing out the cached objects would let one
    step's bookkeeping show up in another's input.
    """
    global _rows_cache
    if _rows_cache is not None and not refresh:
        return [dict(row) for row in _rows_cache]

    svc = _service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A2:Z"
    ).execute()
    rows = []
    for i, values in enumerate(result.get("values", []), start=2):
        padded = values + [""] * (len(HEADERS) - len(values))
        rows.append(_with_aliases({"row_number": i, **dict(zip(HEADERS, padded))}))
    _rows_cache = rows
    return [dict(row) for row in rows]


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
    return _with_aliases({"row_number": row_number, **dict(zip(HEADERS, padded))})


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
    # Anything read back now would predate this write.
    _invalidate()
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
    # Anything read back now would predate this write.
    _invalidate()
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
