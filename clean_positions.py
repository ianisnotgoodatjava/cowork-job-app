"""Repairs Position and Company cells the email parser damaged.

Three distinct failures, all from titles extracted out of prose:

  * **Leading filler.** The capture started one phrase too early, so the sheet
    holds "job Analyst", "post of Leveraged Debt Capital Markets Analytics",
    "open position, Power Trade Analyst, at bp".
  * **A dropped first letter.** An off-by-one left "nalyst, Fixed Income
    Trading" and "nternal Wholesaler". Only repaired for the handful of
    fragments that are unambiguous — guessing the missing letter in general
    would invent titles.
  * **A title in the Company column.** "Risk Analyst at Fanatics" is a
    subject line, not an employer.

Everything here is a repair of text already in the sheet. Nothing is fetched,
so a cell it cannot fix confidently is left exactly as it is.
"""

import re

import sheets

# Phrases the capture regexes swallowed before the actual title.
_LEAD_FILLER_RE = re.compile(
    r"^(?:"
    r"the\s+position\s+(?:of\s+)?|the\s+post\s+of\s+|post\s+of\s+|"
    r"learning\s+more\s+about\s+|open\s+position[,:]?\s*|"
    r"application\s*[-–]\s*|(?:the|a|an)\s+(?:job|role|position)\s+|"
    r"(?:job|role|position)\s+|the\s+"
    r")",
    re.I,
)

# Trailing prose the same captures dragged along.
_TAIL_RE = re.compile(
    r"\s*[,;]?\s*(?:at|with)\s+[A-Za-z0-9].*$"
    r"|\s+position(?:s)?\b.*$"
    # Job-board furniture scraped in with the title: "... Arlington, VA Apply
    # with resume & profile View".
    r"|\s+(?:apply|view|save|share)\b.*$"
    r"|\s+United States\s*\(US\)\s*$"
    r"|\s*[,;]\s*$",
    re.I,
)

# Fragments missing their first letter. Deliberately an explicit list: there
# is no safe general rule for restoring a character that is simply gone.
_DROPPED_FIRST = {
    "nalyst": "Analyst",
    "nternal": "Internal",
    "ulti-asset": "Multi-Asset",
    "ssociate": "Associate",
    "rader": "Trader",
    "nvestment": "Investment",
}


def repair_position(pos: str) -> str:
    """Best repair of a damaged title, or the original if nothing is safe."""
    original = (pos or "").strip()
    if not original:
        return original
    out = original
    previous = None
    while out != previous:
        previous = out
        out = _LEAD_FILLER_RE.sub("", out).strip()
    out = _TAIL_RE.sub("", out).strip(" -–—,:;.")

    first = out.split(",")[0].split()[0].lower() if out.split() else ""
    if first in _DROPPED_FIRST:
        out = _DROPPED_FIRST[first] + out[len(first):]

    # "market analyst" and "junior trade/trade support" are titles that lost
    # their capitalisation, not their words.
    if out and out == out.lower():
        def cap(word):
            if word in ("of", "and", "the", "for"):
                return word
            # Split on the separators inside a title so "trade/trade support"
            # does not come back as "Trade/trade Support".
            return re.sub(r"(^|[/\-])(\w)",
                          lambda m: m.group(1) + m.group(2).upper(), word)
        out = " ".join(cap(w) for w in out.split())

    return out if len(out) >= 4 else original


def run(dry_run: bool = True) -> None:
    rows = sheets.list_rows()
    patches: dict[int, dict] = {}
    pos_fix, co_fix = [], []

    for row in rows:
        rn = row["row_number"]
        pos = (row.get("Position") or "").strip()
        fixed = repair_position(pos)
        if fixed and fixed != pos:
            patches.setdefault(rn, {})["Position"] = fixed
            pos_fix.append((rn, pos, fixed))

        company = (row.get("Company") or "").strip()
        m = re.search(r"^(.*?)\s+at\s+([A-Z0-9][\w&.,'\- ]{2,})$", company)
        if m:
            title, employer = m.group(1).strip(), m.group(2).strip()
            patches.setdefault(rn, {})["Company"] = employer
            co_fix.append((rn, company, employer))
            # The title half is worth keeping only if the row has none.
            current = patches.get(rn, {}).get("Position", pos)
            if title and current.lower() in ("", "analyst"):
                patches.setdefault(rn, {})["Position"] = title
                pos_fix.append((rn, current, title))

    verb = "Would fix" if dry_run else "Fixed"
    print(f"{verb} {len(pos_fix)} Position(s):")
    for rn, old, new in pos_fix:
        print(f"  row {rn:3d} | {old[:44]!r}\n        -> {new[:44]!r}")
    print(f"\n{verb} {len(co_fix)} Company cell(s) holding a title:")
    for rn, old, new in co_fix:
        print(f"  row {rn:3d} | {old[:44]!r} -> {new!r}")

    if dry_run:
        print("\n(dry-run — nothing written)")
        return
    written = sheets.update_rows(patches)
    print(f"\nWrote {written} cell(s) across {len(patches)} row(s).")
