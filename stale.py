"""Ages silent applications out of "applied" so the live pipeline is real.

Nearly every row in the sheet said "Applied", including ones from eighteen
months ago. That makes the column useless for the only question it should
answer — what is still in play — and it inflates every count built on it.

Silence is the signal, so anything that later moved on its own (a rejection, an
interview, an offer) is left exactly as it is. Only rows still sitting on
"applied" past the cutoff are touched, and they become "no response" rather
than "rejected": nobody actually turned you down.
"""

import datetime

import sheets
import status as status_mod
from date_utils import parse_date

DEFAULT_DAYS = 60


def find(rows: list[dict], days: int = DEFAULT_DAYS,
         today: datetime.date | None = None) -> list[tuple[dict, int]]:
    """Rows still on 'applied' whose applied date is older than `days`."""
    today = today or datetime.date.today()
    out = []
    for r in rows:
        if status_mod.normalize(r.get("Status", "")) != status_mod.APPLIED:
            continue
        applied = parse_date(r.get("Applied Draes", ""))
        # An undated row has nothing to age against; leave it rather than
        # guess it is old.
        if not applied:
            continue
        age = (today - applied).days
        if age > days:
            out.append((r, age))
    return out


def run(days: int = DEFAULT_DAYS, dry_run: bool = True) -> int:
    rows = sheets.list_rows()
    stale = find(rows, days)
    live = sum(1 for r in rows
               if status_mod.normalize(r.get("Status", "")) == status_mod.APPLIED)

    print(f"{live} row(s) currently say 'applied'; "
          f"{len(stale)} have been silent longer than {days} days.\n")
    for r, age in sorted(stale, key=lambda p: -p[1]):
        print(f"  {r['row_number']:4d} {r['Company'][:26]:26s} "
              f"{r['Position'][:34]:34s} {r.get('Applied Draes', ''):10s} "
              f"({age} days)")

    if not stale:
        return 0
    print(f"\n{live - len(stale)} application(s) stay live.")
    if dry_run:
        print("(dry run — nothing written)")
        return 0

    svc = sheets._service()
    data = [{"range": f"{sheets.SHEET_TAB}!G{r['row_number']}",
             "values": [[status_mod.NO_RESPONSE]]} for r, _ in stale]
    for i in range(0, len(data), 200):
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sheets.SHEET_ID,
            body={"valueInputOption": "RAW", "data": data[i:i + 200]},
        ).execute()
    print(f"Moved {len(stale)} row(s) to '{status_mod.NO_RESPONSE}'.")
    return len(stale)
