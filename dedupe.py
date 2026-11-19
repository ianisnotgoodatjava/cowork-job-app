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


# Company names arrive truncated from the import — "Graham Capital
# Management, L" is "…, LLC" cut short. That orphan fragment is not a
# recognised suffix, so the name cores to "grahamcapitalmanagementl" while the
# untruncated row cores to "graham", and the two never compare as the same
# company.
_TRUNCATED_SUFFIX_RE = re.compile(r",\s*[A-Za-z]{1,3}\.?\s*$")


def _company_key(row: dict) -> str:
    import company_aliases

    name = _TRUNCATED_SUFFIX_RE.sub("", (row.get("Company") or "").strip())
    # A row the scanner re-imported under the ATS's own spelling is the same
    # application as the one whose name you corrected: "hyperiongrp" and
    # "Howden" were two rows for one job, and keyed on the name alone they
    # could never be seen as a pair.
    name = company_aliases.load().get(normalize(name), name)
    return _core(name).replace(" ", "") or normalize(name).replace(" ", "")


# Trailing requisition noise. The same U.S. Bank application is on the sheet
# three times as "Investment Portfolio Analyst", "... Job ID# 2025" and
# "... Job ID#" — identical roles that compared as different titles because
# the ATS reference was captured inconsistently.
# Moved to job_title: string functions that a sheet-maintenance module
# should not own. Re-exported so existing callers keep working.
from job_title import strip_req  # noqa: F401


# Pure title comparison, moved to job_title so the scanner does not have to
# import a sheet-maintenance module to use it.
from job_title import _role_match  # noqa: F401



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
    pa = normalize(strip_req(a.get("Position", ""))).strip()
    pb = normalize(strip_req(b.get("Position", ""))).strip()
    return _company_key(a) == _company_key(b) and pa == pb and pa != ""


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

    # A title with requisition junk fused onto it is longer but worse:
    # "Business Development Analystrequisition number 75528" beat the clean
    # "Business Development Analyst" purely on length.
    pa, pb = a.get("Position", ""), b.get("Position", "")
    a_clean, b_clean = strip_req(pa) == pa.strip(), strip_req(pb) == pb.strip()
    if a_clean != b_clean:
        return (a, b) if a_clean else (b, a)

    # Then whichever names the role more specifically.
    return (a, b) if len(normalize(pa)) >= len(normalize(pb)) else (b, a)


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
