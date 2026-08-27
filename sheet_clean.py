"""Makes the sheet internally consistent, without changing what it means.

Three things drift on a sheet that is written by several scanners and edited
by hand:

  * Status spelling. "Applied" and "applied" and "Rejected" and "rejected" all
    existed at once, because each writer hardcoded its own literal. Everything
    reads through `status.normalize`, so nothing was broken — it just looked
    it. Rewritten through `status.display`.
  * Blank Network cells. Yes/No is a question with two answers; a blank third
    state means "nobody has looked", which is not what the column records.
  * Stray whitespace in Company and Position, which breaks nothing and makes
    every group-by slightly wrong.

Unrecognized Status text is left exactly as typed. It is your note, and
guessing at it would lose information.
"""

import sheets
import status as status_mod

TEXT_COLUMNS = ("Company", "Position", "location", "Job Link")


def run(dry_run: bool = True) -> None:
    rows = sheets.list_rows()
    status_fix, network_fix, space_fix, unknown = [], [], [], []
    patches: dict[int, dict] = {}

    for row in rows:
        rn, patch = row["row_number"], {}

        raw = (row.get("Status") or "").strip()
        if not raw and (row.get("Applied Draes") or "").strip():
            # A row exists because you applied, so a blank Status is a gap in
            # the record rather than a state. It matters: `status.is_open`
            # reads blank as not-open, so the row silently disappears from
            # follow-ups, fit ranking and every outreach command.
            patch["Status"] = status_mod.display(status_mod.APPLIED)
            status_fix.append((rn, "(blank)", patch["Status"]))
        elif raw:
            shown = status_mod.display(raw)
            if status_mod.normalize(raw) not in status_mod.CANONICAL:
                unknown.append((rn, raw))
            elif shown != row.get("Status"):
                patch["Status"] = shown
                status_fix.append((rn, row.get("Status"), shown))

        net = (row.get("Network") or "").strip()
        if not net:
            patch["Network"] = "No"
            network_fix.append(rn)
        elif net.upper() == "TRUE":
            patch["Network"] = "Yes"
            network_fix.append(rn)
        elif net not in ("Yes", "No"):
            patch["Network"] = "Yes" if net.upper() == "YES" else "No"
            network_fix.append(rn)

        for col in TEXT_COLUMNS:
            val = row.get(col)
            if isinstance(val, str) and val != val.strip():
                patch[col] = val.strip()
                space_fix.append((rn, col))

        if patch:
            patches[rn] = patch

    written = 0 if dry_run else sheets.update_rows(patches)

    verb = "Would fix" if dry_run else "Fixed"
    print(f"{verb} {len(status_fix)} Status spelling(s):")
    for rn, old, new in status_fix[:6]:
        print(f"  row {rn:3d} | {old!r} -> {new!r}")
    if len(status_fix) > 6:
        print(f"  ... and {len(status_fix) - 6} more")

    print(f"\n{verb} {len(network_fix)} Network cell(s) to an explicit Yes/No.")
    print(f"{verb} {len(space_fix)} cell(s) with stray whitespace.")

    if unknown:
        print(f"\n{len(unknown)} row(s) have Status text I do not recognize "
              f"and left untouched:")
        for rn, raw in unknown:
            print(f"  row {rn:3d} | {raw!r}")
    if dry_run:
        print("\n(dry-run — nothing written)")
    else:
        print(f"\nWrote {written} cell(s) across {len(patches)} row(s) "
              f"in one batched call.")
