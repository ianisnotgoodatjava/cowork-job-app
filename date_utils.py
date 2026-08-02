"""Parses the sheet's existing date formats: 'M/D/YYYY' (e.g. '6/27/2025'),
bare 'M/D' with no year (a lot of rows are entered this way — year is
inferred as the current year, or last year if that would land in the
future), and ISO 'YYYY-MM-DD'. Returns None if unparseable/blank."""

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

    try:
        today = datetime.date.today()
        parsed = datetime.datetime.strptime(value, "%m/%d").replace(year=today.year).date()
        if parsed > today:
            parsed = parsed.replace(year=today.year - 1)
        return parsed
    except ValueError:
        return None
