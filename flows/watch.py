"""The every-few-hours cycle: new application in, researched outreach out.

One pass does what used to be four separate commands and a lot of manual
work in between:

  1. read Gmail for application confirmations that are not on the sheet yet,
     and add them;
  2. pick up rejections and replies that arrived since the last pass;
  3. for each genuinely new application, search the web for the live posting,
     write the URL into the Job Link column, find a mid-level person on that
     team, and draft an outreach email to them;
  4. email you a short note if anything is waiting on a reply.

Step 3 is the only part that costs money, and it is the only part that needs
MODEL_API_KEY. Without the key the pass still does 1, 2 and 4 and says
so — a scheduled job that half-works and reports it beats one that dies.

Nothing here sends mail on your behalf. Outreach lands as Gmail *drafts*;
the only message that leaves is the reminder to your own address.
"""

import datetime

from sources import app_scanner
from flows import auto_pipeline
from sources import muse
from sources import rejection_scanner
from sheetops import sheet_format
from store import sheets


def _row_key(row: dict) -> str:
    """Identity that survives the sheet being re-sorted and renumbered."""
    return "|".join((
        (row.get("Company") or "").strip().lower(),
        (row.get("Position") or "").strip().lower(),
        (row.get("Applied Draes") or "").strip(),
    ))


def run(days_back: int = 14, limit: int = 3, min_fit: float = 2.0,
        dry_run: bool = False, draft: bool = True,
        deep: bool | None = None, taiwan: bool = True) -> None:
    # The maintenance sweeps re-read the whole mailbox and cost minutes.
    # Running them on every pass would triple the work for changes that
    # only accumulate slowly, so they run once a day, on the late pass.
    if deep is None:
        deep = datetime.datetime.now().hour >= 22
    step = auto_pipeline._step  # same retry-then-skip behaviour as the nightly run

    print("=" * 68)
    print(f"WATCH — {datetime.datetime.now():%Y-%m-%d %H:%M}"
          f"{'  (dry-run)' if dry_run else ''}")
    print("=" * 68)

    # The first network call of the pass, so it is the one that meets a
    # network still waking up. Every step below reads the sheet; if it is
    # still unreachable after the retry, there is nothing this pass can do.
    rows = step(sheets.list_rows)
    if rows is None:
        print("\n  sheet unreachable -- pass abandoned, next scheduled run will retry")
        return
    # Keyed on content, not row number: every pass re-sorts the sheet, so a
    # row number identifies a different application afterwards. Counting
    # those gave the net change, which read as "0 added" on a pass that added
    # three rows and removed three duplicates.
    before = {_row_key(r) for r in rows}

    print("\n[1/7] New applications from Gmail")
    step(app_scanner.scan, days_back=days_back, dry_run=dry_run)

    print("\n[2/7] Rejections and replies")
    step(rejection_scanner.scan, days_back=max(days_back, 90), dry_run=dry_run)
    from sources import reply_scanner
    step(reply_scanner.scan, days_back=max(days_back, 120), dry_run=dry_run,
         use_calendar=False, notify=not dry_run)

    after = step(sheets.list_rows)
    if after is None:
        # Falling back to the rows read before the scanners ran reported
        # "0 row(s) added" whenever this read failed — a wrong number that
        # looked like a true one. Eight rows had in fact been added.
        print("\n  (could not re-read the sheet; this pass's row count is "
              "unknown, but the scanners above reported what they did)")
    else:
        now = {_row_key(r) for r in after}
        added, removed = now - before, before - now
        parts = []
        if added:
            parts.append(f"{len(added)} row(s) added")
        if removed:
            parts.append(f"{len(removed)} removed")
        print(f"\n  {' and '.join(parts) if parts else 'no row changes'} "
              f"this pass")

    # These two are why a new row is useful the moment it lands: the posting
    # link, then what the posting actually says. Both are plain HTTP, so they
    # run on every pass whether or not the API key is set.
    print("\n[3/7] Job posting links")
    from sources import link_finder
    step(link_finder.scan, days_back=max(days_back, 520), dry_run=dry_run)

    print("\n[4/7] Reading postings: qualifications, posting date, location")
    if dry_run:
        print("  (dry-run — skipped)")
    else:
        from sheetops import enrich
        step(enrich.run, limit=60, dry_run=False)

    print("\n[5/7] Researching postings and drafting outreach")
    if not muse.available():
        print("  skipped — no MODEL_API_KEY in .env.\n"
              "  Everything else in this pass ran.")
    elif not draft:
        print("  skipped — drafting disabled for this pass")
    else:
        from outreach import auto_draft
        # new_only means a row is researched once, ever. Re-running the watch
        # every few hours over the same sheet would otherwise re-bill for
        # work already done.
        step(auto_draft.run, limit=limit, min_fit=min_fit, dry_run=dry_run,
             create_drafts=not dry_run, new_only=True, write_link=not dry_run)

    print("\n[5b/7] Drafts to companies that already said no")
    if dry_run:
        print("  (dry-run — skipped)")
    else:
        from outreach import draft_cleanup
        step(draft_cleanup.run, dry_run=False)

    # One request per new application, once ever: who at the firm shares a
    # Taiwan background, nearest the role. Runs right after the row lands,
    # while the application is fresh enough for an introduction to matter.
    print("\n[5c/7] Taiwan connections at new applications")
    if not taiwan:
        print("  skipped (--no-taiwan)")
    else:
        from people import taiwan_connections
        step(taiwan_connections.sweep, days_back=days_back, limit=5,
             dry_run=dry_run)

    print("\n[6/7] Housekeeping"
          f"{' (deep — once a day)' if deep else ' (light)'}")
    if dry_run:
        print("  (dry-run — skipped)")
    else:
        from sheetops import sheet_clean
        step(sheet_clean.run, dry_run=False)
        if deep:
            # Everything that only pays off over time, and is too slow to run
            # every few hours.
            from sheetops import dedupe as dedupe_mod
            from sheetops import fix_positions
            from sheetops import location_backfill
            from sheetops import jd_backfill
            from people import network_audit
            from outreach import reoutreach
            step(dedupe_mod.run, dry_run=False, max_day_gap=30)
            step(jd_backfill.run, dry_run=False, limit=25, days_back=520)
            step(reoutreach.scan, days_back=400, min_silent_days=21)
            step(fix_positions.run, dry_run=False, days_back=520)
            step(location_backfill.run, dry_run=False)
            step(network_audit.run, days_back=460, dry_run=False)
            if muse.available():
                from sources import job_finder
                step(job_finder.run)
            else:
                print("  find-jobs skipped — no MODEL_API_KEY set")

    print("\n[7/8] Sort and colour")
    if dry_run:
        print("  (dry-run — skipped)")
    else:
        step(sheets.sort_by_applied_date)
        step(sheet_format.apply_status_colors)

    print("\n[8/8] Applications still without a draft")
    from people import findability
    step(findability.run_coverage, days_back=60)

    # And who to write to. Naming the gap is only half of it -- the sourcing
    # modules existed but were reachable only by typing a command, so they
    # contributed nothing to the scheduled run. BrokerCheck is one fast
    # request per firm, so the roster is affordable here; EDGAR has to parse
    # whole filings and stays manual.
    if not dry_run:
        from people import sourcing
        _have, missing = findability.coverage(days_back=60)
        step(sourcing.worklist, [row for row, _tier in missing], limit=12)

    print("\nPass complete.")
