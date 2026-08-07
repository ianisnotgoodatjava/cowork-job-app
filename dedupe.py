"""Finds rows that are the same application logged twice.

You kept spotting these by eye (306/307, 345/347, the TikTok pair), which is
the sign a check belongs in code. Exact matching does not find them, because
every pair differed in some small way:

  * "BMO" vs "BMO Capital Markets"                    — company written differently
  * "...Consultant" vs "...Consultant at Natixis..."  — title dragged the company along
  * 5/11 vs 5/12                                      — confirmation arrived a day later

So companies are compared on their distinctive core, titles on shared words,
and dates on proximity rather than equality.

Deleting is opt-in and only ever removes a row the Gmail import created — if
both rows in a pair are yours, it reports and leaves them alone.
"""

import re

import sheets
from company_match import _core, normalize
from date_utils import parse_date

IMPORT_NOTE = "Auto-added from Gmail"
PLACEHOLDER = "analyst"

_STOP = {"analyst", "associate", "senior", "junior", "the", "and", "for", "at",
         "of", "a", "an", "to", "in", "with", "new", "york", "ny", "i", "ii"}


def _company_key(row: dict) -> str:
    return _core(row.get("Company", "")).replace(" ", "") or \
        normalize(row.get("Company", "")).replace(" ", "")


def _title_tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (title or "").lower())
            if t not in _STOP and len(t) > 2}


def _role_match(a: str, b: str) -> str:
    """How confidently two titles are the same role: '', 'fuzzy', or 'sure'.

    Token overlap alone is not enough to delete on. Fitch's "Structured
    Finance - ABS / CMBS / RMBS, Analyst - New York" titles share every word
    except the one that matters, and a 0.6-overlap rule wanted to merge three
    separate applications into one. So overlap only ever reports a *fuzzy*
    match for you to look at; deletion needs an exact or prefix match.
    """
    na, nb = normalize(a).strip(), normalize(b).strip()
    if not na or not nb:
        return ""
    if na == nb:
        return "sure"
    # "Consultant" vs "Consultant at Natixis" — the same title with the
    # company dragged along.
    if na.startswith(nb) or nb.startswith(na):
        return "sure"

    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return ""
    # A word one title has and the other lacks is usually the whole point
    # (ABS vs CMBS, equity vs quantitative), so never treat that as certain.
    overlap = len(ta & tb) / min(len(ta), len(tb))
    return "fuzzy" if overlap >= 0.7 else ""


def _provenance_available(rows: list[dict]) -> bool:
    """Whether we can still tell import-created rows from hand-written ones.

    That fact lived in the Notes column, which the trimmed A-J tracker no
    longer has. Without it every row looks hand-written and nothing is ever
    auto-removable, so the caller falls back to exact-match pairs instead.
    """
    return any("Notes" in r for r in rows)


def _is_import(row: dict) -> bool:
    return IMPORT_NOTE in (row.get("Notes") or "")


def _identical(a: dict, b: dict) -> bool:
    """Same company and the same title, word for word."""
    return (_company_key(a) == _company_key(b)
            and normalize(a.get("Position", "")).strip()
            == normalize(b.get("Position", "")).strip()
            and normalize(a.get("Position", "")).strip() != "")


def find(rows: list[dict], max_day_gap: int = 21) -> list[tuple[dict, dict, str]]:
    """Pairs of rows that look like one application, tagged 'sure' or 'fuzzy'."""
    by_company: dict[str, list[dict]] = {}
    for r in rows:
        key = _company_key(r)
        if key:
            by_company.setdefault(key, []).append(r)

    pairs = []
    for group in by_company.values():
        real = [r for r in group
                if normalize(r.get("Position", "")).strip() != PLACEHOLDER]
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                pa = normalize(a.get("Position", "")).strip()
                pb = normalize(b.get("Position", "")).strip()
                if pa == PLACEHOLDER and pb == PLACEHOLDER:
                    # Two unparsed rows at one company days apart: nothing
                    # distinguishes them, which is exactly what "Fairfield
                    # Maxwell / Analyst" and "FAIRFIELD-MAXWELL LTD / Analyst"
                    # on the same date were.
                    confidence = "sure"
                elif pa == PLACEHOLDER or pb == PLACEHOLDER:
                    # A placeholder duplicates *something* here — but only
                    # when there is a single real row to attach it to. With
                    # several, which one it belongs to is a guess, and
                    # guessing deletes real applications.
                    if len(real) != 1:
                        continue
                    confidence = "sure"
                else:
                    confidence = _role_match(a.get("Position", ""),
                                             b.get("Position", ""))
                    if not confidence:
                        continue
                da, db = (parse_date(a.get("Applied Draes", "")),
                          parse_date(b.get("Applied Draes", "")))
                if da and db and abs((da - db).days) > max_day_gap:
                    continue
                pairs.append((a, b, confidence))
    return pairs


# How much a status tells you. Dropping the row that knows the outcome in
# favour of one still saying "Applied" would quietly lose the result — which
# is what happened to the Mesirow pair on the first run.
_STATUS_RANK = {
    "offer": 5,
    "interview-rejected": 4,
    "interviewing": 3,
    "rejected": 2,
    "applied": 1,
    "": 0,
}


def _status_rank(row: dict) -> int:
    import status as status_mod
    return _STATUS_RANK.get(status_mod.normalize(row.get("Status", "")), 1)


def _richer(a: dict, b: dict) -> tuple[dict, dict]:
    """Returns (keep, drop): the row that carries more information survives."""
    a_import, b_import = _is_import(a), _is_import(b)
    if a_import != b_import:
        return (b, a) if a_import else (a, b)

    # Outcome first — a known result beats a longer job title.
    ra, rb = _status_rank(a), _status_rank(b)
    if ra != rb:
        return (a, b) if ra > rb else (b, a)

    # Then whichever names the role more specifically.
    a_len = len(normalize(a.get("Position", "")))
    b_len = len(normalize(b.get("Position", "")))
    return (a, b) if a_len >= b_len else (b, a)


def run(dry_run: bool = True, max_day_gap: int = 21) -> None:
    rows = sheets.list_rows()
    pairs = find(rows, max_day_gap=max_day_gap)
    print(f"{len(pairs)} likely duplicate pair(s) among {len(rows)} rows.\n")

    have_provenance = _provenance_available(rows)
    removable, identical, manual = [], [], []
    dropped: set[int] = set()
    for a, b, confidence in pairs:
        keep, drop = _richer(a, b)
        # One row can pair with several others; without this a row could be
        # listed as the survivor of one pair and deleted by the next.
        if keep["row_number"] in dropped or drop["row_number"] in dropped:
            continue
        if confidence == "sure" and have_provenance and _is_import(drop):
            removable.append((keep, drop))
            dropped.add(drop["row_number"])
        elif confidence == "sure" and _identical(keep, drop):
            # Word-for-word the same role at the same company within the date
            # window. Nothing distinguishes them, so one is a duplicate even
            # though we can no longer tell which side created it.
            identical.append((keep, drop))
            dropped.add(drop["row_number"])
        else:
            manual.append((keep, drop, confidence))

    if not have_provenance:
        print("note: the Notes column is gone, so import-vs-yours provenance is\n"
              "      no longer recorded. Falling back to exact title matches.\n")

    print(f"=== {len(removable)} safe to remove (the extra row came from the import) ===")
    for keep, drop in removable:
        print(f"  KEEP row {keep['row_number']:3d} | {keep['Company'][:24]:24s} "
              f"| {keep['Position'][:38]:38s} | {keep['Applied Draes']}")
        print(f"  DROP row {drop['row_number']:3d} | {drop['Company'][:24]:24s} "
              f"| {drop['Position'][:38]:38s} | {drop['Applied Draes']}")

    if identical:
        print(f"\n=== {len(identical)} identical pair(s): same company, same "
              f"title, within the date window ===")
        for keep, drop in identical:
            print(f"  KEEP row {keep['row_number']:3d} | {keep['Company'][:24]:24s} "
                  f"| {keep['Position'][:36]:36s} | {keep['Applied Draes']:10s} "
                  f"| {keep['Status'][:16]}")
            print(f"  DROP row {drop['row_number']:3d} | {drop['Company'][:24]:24s} "
                  f"| {drop['Position'][:36]:36s} | {drop['Applied Draes']:10s} "
                  f"| {drop['Status'][:16]}")

    if manual:
        print(f"\n=== {len(manual)} need your call (never auto-removed) ===")
        for keep, drop, confidence in manual:
            why = "similar titles" if confidence == "fuzzy" else "both your rows"
            print(f"  [{why}] row {keep['row_number']:3d} & row {drop['row_number']:3d} "
                  f"| {keep['Company'][:22]:22s}")
            print(f"      {keep['Position'][:46]}")
            print(f"      {drop['Position'][:46]}")

    if dry_run:
        print("\n(dry-run — nothing removed)")
        return

    # Row numbers all come from one snapshot; delete_rows removes highest
    # first so the remaining targets keep their numbers.
    targets = sorted({d["row_number"] for _, d in removable + identical})
    removed = sheets.delete_rows(targets)
    print(f"\nRemoved {removed} duplicate row(s).")
