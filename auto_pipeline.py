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


def _step(fn, *args, **kwargs):
    """Runs one pipeline step, surviving a transient failure.

    The scheduled run fires whether or not the laptop is awake and online. A
    2026-08-03 run died on a DNS failure resolving oauth2.googleapis.com and
    took the whole pipeline with it, leaving a stack trace and no work done.
    One retry covers a network that is still waking up; anything worse is
    logged as a skipped step so the remaining steps still run.
    """
    import time

    for attempt in (1, 2):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            transient = isinstance(exc, (OSError,)) or any(
                s in type(exc).__name__ for s in ("Transport", "Connection", "Timeout")
            ) or "resolve" in str(exc).lower()
            if attempt == 1 and transient:
                print(f"  ! {type(exc).__name__}: {exc}\n  retrying in 60s...")
                time.sleep(60)
                continue
            print(f"  ! step skipped — {type(exc).__name__}: {str(exc)[:200]}")
            return None


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

    print("\n[1/6] New applications from Gmail")
    _step(app_scanner.scan, days_back=days_back, dry_run=dry_run)

    print("\n[1b/6] Job Link backfill")
    # Runs before the Job Description backfill (step 4) so a link found here
    # is available as that step's first, best source the same day.
    import link_finder
    _step(link_finder.scan, days_back=max(days_back, 365), dry_run=dry_run)

    print("\n[2/6] Rejections from Gmail")
    _step(rejection_scanner.scan, days_back=days_back, dry_run=dry_run)

    print("\n[3/6] Interview invites, replies and networking")
    # Runs after rejections so a row that ended in a rejection is upgraded to
    # interview-rejected rather than dragged back to interviewing.
    import reply_scanner
    _step(reply_scanner.scan, days_back=max(days_back, 120), dry_run=dry_run,
          notify=not dry_run)

    print("\n[3b/6] Warm leads that went cold")
    import reoutreach
    leads = _step(reoutreach.scan, days_back=max(days_back, 400),
                  min_silent_days=21) or []
    # Only the warm ones are worth waking you up for: someone at the company
    # offered a call and it was never followed up.
    warm = [l for l in leads if l["warm"]][:8]
    if warm and not dry_run:
        import notify
        _step(notify.send_reachout_reminder, [
            {
                "key": f"reoutreach:{l['domain']}:{l['last_contact']}",
                "kind": "warm lead gone cold",
                "company": l["company"],
                "role": "",
                "who": sorted(l["repliers"])[0][:50],
                "detail": f"{l['silent']} days silent, no rejection"
                          + ("" if l["row"] else " — NOT IN SHEET"),
                "row": l["row"] or "",
            }
            for l in warm
        ])

    print("\n[4/6] Job Description backfill")
    import jd_backfill
    _step(jd_backfill.run, dry_run=dry_run, limit=25, days_back=max(days_back, 365))

    print("\n[5/6] Sort + colour")
    if dry_run:
        print("(dry-run — skipped)")
    else:
        _step(sheets.sort_by_applied_date)
        _step(sheet_format.apply_status_colors)

    print("\n[6/6] Ready for outreach (no contact saved yet)")
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
