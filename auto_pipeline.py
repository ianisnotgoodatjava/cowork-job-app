"""End-to-end pipeline run on a schedule.

  1. find new application-confirmation emails and add them to the sheet
  2. detect rejection emails and mark those rows rejected
  3. sort by applied date and re-apply the status colours
  4. report which rows are ready for outreach

Step 4 deliberately stops at reporting. Finding the right person at a
company needs web search and a judgement call about seniority and team fit
that this script cannot make on its own — and a draft to the wrong person,
sent under your name, is worse than no draft. Run the reported rows through
the outreach flow (`outreach-examples` -> `draft-outreach-email`) with
Claude, which can do the search and the judging.
"""

import datetime

import app_scanner
import rejection_scanner
import sheet_format
import sheets
import status as status_mod
from company_match import normalize
from date_utils import parse_date


def _ready_for_outreach(days_back: int) -> list[dict]:
    """Applied rows, recent, with no contact saved yet."""
    cutoff = datetime.date.today() - datetime.timedelta(days=days_back)
    out = []
    for r in sheets.list_rows():
        # Rejected/interview-rejected rows are skipped: no point networking
        # into a role that is already closed.
        if not status_mod.is_open(r.get("Status", "")):
            continue
        if r.get("Contact Name", "").strip():
            continue
        d = parse_date(r.get("Applied Draes", ""))
        if not d or d < cutoff:
            continue
        out.append(r)
    return out


def run(days_back: int = 30, dry_run: bool = False) -> None:
    print("=" * 70)
    print(f"AUTO PIPELINE — {datetime.date.today().isoformat()}"
          f"{'  (dry-run)' if dry_run else ''}")
    print("=" * 70)

    print("\n[1/4] New applications from Gmail")
    app_scanner.scan(days_back=days_back, dry_run=dry_run)

    print("\n[2/4] Rejections from Gmail")
    rejection_scanner.scan(days_back=days_back, dry_run=dry_run)

    print("\n[3/4] Sort + colour")
    if dry_run:
        print("(dry-run — skipped)")
    else:
        sheets.sort_by_applied_date()
        sheet_format.apply_status_colors()

    print("\n[4/4] Ready for outreach (no contact saved yet)")
    ready = _ready_for_outreach(days_back)
    if not ready:
        print("  none")
        return

    # Companies already contacted are worth flagging rather than hiding: a
    # second person on a different team is often still worth reaching, but
    # you should know it is not a first touch.
    try:
        import outreach_corpus
        contacted = {normalize(r.get("company", "")) for r in outreach_corpus.load()}
        contacted.discard("")
    except FileNotFoundError:
        contacted = set()

    for r in ready:
        c = normalize(r["Company"])
        seen = any(k and (k in c or c in k) for k in contacted)
        mark = "  [already emailed someone there]" if seen else ""
        print(f"  row {r['row_number']:4d} | {r['Company'][:30]:30s} | "
              f"{r['Position'][:40]}{mark}")

    print(f"\n{len(ready)} row(s) ready. To draft outreach for one:")
    print("  python cli.py outreach-examples --row <N>")
    print("  python cli.py draft-outreach-email --row <N> --to <email> "
          "--subject <...> --body-file <...> --attach ~/Ian_Liu_Resume.pdf")
