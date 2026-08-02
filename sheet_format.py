"""Color-codes tracker rows by Status: red = rejected, green = interview/offer.

Applied rows are left with no fill so the highlights stay meaningful — the
sheet already used red-for-rejected by hand before this project, so this
keeps that convention and just makes it automatic.
"""

import os
from googleapiclient.discovery import build
from google_auth import get_credentials
import sheets

RED = {"red": 0.96, "green": 0.80, "blue": 0.80}
GREEN = {"red": 0.80, "green": 0.94, "blue": 0.80}
NONE = {"red": 1.0, "green": 1.0, "blue": 1.0}

STATUS_COLORS = {
    "rejected": RED,
    "interview": GREEN,
    "offer": GREEN,
}


def _tab_id(svc, sheet_id: str, tab_name: str) -> int:
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    raise ValueError(f"Tab not found: {tab_name}")


def apply_status_colors(dry_run: bool = False) -> None:
    svc = build("sheets", "v4", credentials=get_credentials())
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    tab = os.environ.get("GOOGLE_SHEET_TAB", "application")
    tab_id = _tab_id(svc, sheet_id, tab)

    rows = sheets.list_rows()
    n_cols = len(sheets.HEADERS)

    requests = []
    counts = {"rejected": 0, "interview/offer": 0, "cleared": 0}
    for r in rows:
        status = r.get("Status", "").strip().lower()
        color = STATUS_COLORS.get(status, NONE)
        if color is RED:
            counts["rejected"] += 1
        elif color is GREEN:
            counts["interview/offer"] += 1
        else:
            counts["cleared"] += 1
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": tab_id,
                    "startRowIndex": r["row_number"] - 1,
                    "endRowIndex": r["row_number"],
                    "startColumnIndex": 0,
                    "endColumnIndex": n_cols,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": color}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        })

    print(f"rejected (red): {counts['rejected']} | interview/offer (green): "
          f"{counts['interview/offer']} | no fill: {counts['cleared']}")
    if dry_run:
        print("(dry-run — no formatting written)")
        return

    for i in range(0, len(requests), 100):
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body={"requests": requests[i:i + 100]}
        ).execute()
    print(f"Applied formatting to {len(requests)} rows.")
