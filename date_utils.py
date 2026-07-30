"""Parses the sheet's existing date format (M/D/YYYY, e.g. '6/27/2025'),
falling back to ISO (YYYY-MM-DD). Returns None if unparseable/blank."""

import datetime


def parse_date(value: str) -> datetime.date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None
