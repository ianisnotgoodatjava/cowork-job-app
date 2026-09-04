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
ANTHROPIC_API_KEY. Without the key the pass still does 1, 2 and 4 and says
so — a scheduled job that half-works and reports it beats one that dies.

Nothing here sends mail on your behalf. Outreach lands as Gmail *drafts*;
the only message that leaves is the reminder to your own address.
"""

import datetime
import os

import app_scanner
import auto_pipeline
import rejection_scanner
import sheet_format
import sheets


def run(days_back: int = 14, limit: int = 3, min_fit: float = 2.0,
        dry_run: bool = False, draft: bool = True,
        deep: bool | None = None) -> None:
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

    before = {r["row_number"] for r in sheets.list_rows()}

    print("\n[1/7] New applications from Gmail")
    step(app_scanner.scan, days_back=days_back, dry_run=dry_run)

    print("\n[2/7] Rejections and replies")
    step(rejection_scanner.scan, days_back=max(days_back, 90), dry_run=dry_run)
    import reply_scanner
    step(reply_scanner.scan, days_back=max(days_back, 120), dry_run=dry_run,
         use_calendar=False, notify=not dry_run)

    after = sheets.list_rows()
    added = len(after) - len(before)
    print(f"\n  {added} row(s) added this pass")

    # These two are why a new row is useful the moment it lands: the posting
    # link, then what the posting actually says. Both are plain HTTP, so they
    # run on every pass whether or not the API key is set.
    print("\n[3/7] Job posting links")
    import link_finder
    step(link_finder.scan, days_back=max(days_back, 520), dry_run=dry_run)

    print("\n[4/7] Reading postings: qualifications, posting date, location")
    if dry_run:
        print("  (dry-run — skipped)")
    else:
        import enrich
        step(enrich.run, limit=60, dry_run=False)

    print("\n[5/7] Researching postings and drafting outreach")
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("  skipped — no ANTHROPIC_API_KEY set.\n"
              "  Everything else in this pass ran. Add the key to .env to turn\n"
              "  on posting lookup and drafted outreach.")
    elif not draft:
        print("  skipped — drafting disabled for this pass")
    else:
        import auto_draft
        # new_only means a row is researched once, ever. Re-running the watch
        # every few hours over the same sheet would otherwise re-bill for
        # work already done.
        step(auto_draft.run, limit=limit, min_fit=min_fit, dry_run=dry_run,
             create_drafts=not dry_run, new_only=True, write_link=not dry_run)

    print("\n[5b/7] Drafts to companies that already said no")
    if dry_run:
        print("  (dry-run — skipped)")
    else:
        import draft_cleanup
        step(draft_cleanup.run, dry_run=False)

    print("\n[6/7] Housekeeping"
          f"{' (deep — once a day)' if deep else ' (light)'}")
    if dry_run:
        print("  (dry-run — skipped)")
    else:
        import sheet_clean
        step(sheet_clean.run, dry_run=False)
        if deep:
            # Everything that only pays off over time, and is too slow to run
            # every few hours.
            import dedupe as dedupe_mod
            import fix_positions
            import location_backfill
            import jd_backfill
            import network_audit
            import reoutreach
            step(dedupe_mod.run, dry_run=False, max_day_gap=30)
            step(jd_backfill.run, dry_run=False, limit=25, days_back=520)
            step(reoutreach.scan, days_back=400, min_silent_days=21)
            step(fix_positions.run, dry_run=False, days_back=520)
            step(location_backfill.run, dry_run=False)
            step(network_audit.run, days_back=460, dry_run=False)
            if os.environ.get("ANTHROPIC_API_KEY") or \
                    os.environ.get("ANTHROPIC_AUTH_TOKEN"):
                import job_finder
                step(job_finder.run)
            else:
                print("  find-jobs skipped — no ANTHROPIC_API_KEY set")

    print("\n[7/8] Sort and colour")
    if dry_run:
        print("  (dry-run — skipped)")
    else:
        step(sheets.sort_by_applied_date)
        step(sheet_format.apply_status_colors)

    print("\n[8/8] Applications still without a draft")
    import findability
    step(findability.run_coverage, days_back=60)

    print("\nPass complete.")
