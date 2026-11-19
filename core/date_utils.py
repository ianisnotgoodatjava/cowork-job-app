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

    # Supply the year before parsing rather than after. Letting strptime
    # default the year is deprecated in 3.13 and changes behaviour in 3.15,
    # and its placeholder year is not a leap year, so "2/29" failed to parse
    # at all.
    try:
        today = datetime.date.today()
        parsed = datetime.datetime.strptime(
            f"{value}/{today.year}", "%m/%d/%Y").date()
    except ValueError:
        return None
    if parsed > today:
        try:
            parsed = parsed.replace(year=today.year - 1)
        except ValueError:          # 29 Feb in a non-leap previous year
            return None
    return parsed
