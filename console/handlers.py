"""The sixty-one handlers, one per command.

They are adapters and nothing else: read an argparse Namespace, call into a
layer, print. Forty-five of them are four lines long. The logic lives in the
layer packages, where it can be imported without argparse.

They sat in cli.py alongside 378 lines of parser construction, which is why
neither half was readable. console/commands.py names each of these as a
string, and tests/test_console.py resolves every one — so renaming a handler
and forgetting the table is a failing test rather than a command that breaks
the first time someone runs it, months later.

The imports inside function bodies are deliberate and were already here. They
began as a way to break import cycles; the layering removed the cycles, and
what they buy now is startup cost: cli.py --help answers in a fifth of a
second without importing Gmail, Sheets or scikit-learn.
"""

from dotenv import load_dotenv

load_dotenv()

from store import sheets
from core import status as status_mod
from store import gmail_client
from core import linkedin_helper
from sources import rejection_scanner
from sources import link_finder
from sources import app_scanner
from flows import auto_pipeline
from outreach import draft_cleanup
from outreach import follow_up
from sheetops import sheet_format
from sheetops import stale
from core.date_utils import parse_date
from core.config import NETWORKING_KEYWORDS
from outreach import email_drafter


def cmd_log_application(args):
    from sources import apply_capture
    from core.company_match import _core

    title, company = args.job_title or "", args.company or ""
    location, link = args.location or "", args.link or args.url or ""
    requirements = args.requirements or ""

    if args.url or args.paste_file:
        pasted = ""
        if args.paste_file:
            with open(args.paste_file) as f:
                pasted = f.read()
        got = apply_capture.capture(url=args.url or "", pasted=pasted)
        # Anything you passed explicitly wins over what the page said.
        title = title or got["title"]
        company = company or got["company"]
        location = location or got["location"]
        requirements = requirements or apply_capture.requirements_cell(got["requirements"])
        print(f"Read the posting via {got['source']}:")
        for label, value in (("title", title), ("company", company),
                             ("location", location)):
            print(f"  {label:9s} {value or '(not found)'}")
        print(f"  {'skills':9s} {len(got['requirements'])} bullet(s) captured")
        if got["thin"]:
            print("\n  The page came back with almost no text — Workday and LinkedIn\n"
                  "  render postings in JavaScript. Copy the posting and re-run with\n"
                  "  --paste-file, or pass the fields yourself.")

    if not title or not company:
        raise SystemExit("Need a title and a company: pass --job-title/--company, "
                         "or a --url the posting can actually be read from.")

    # A second application to the same role is nearly always a mis-run, and the
    # sheet spent months carrying pairs like this before anyone noticed.
    key = (_core(company).replace(" ", ""), " ".join(title.lower().split()))
    for r in sheets.list_rows():
        if (_core(r["Company"]).replace(" ", ""),
                " ".join((r["Position"] or "").lower().split())) == key:
            print(f"\nAlready on row {r['row_number']}: "
                  f"{r['Position']} @ {r['Company']} ({r.get('Applied Draes', '')})")
            if not args.yes and input("Log it again anyway? [y/N] ").strip().lower() != "y":
                raise SystemExit("Not logged.")
            break

    # Confirm whenever the fields were read off a page rather than typed, since
    # that is when they can be wrong without you noticing.
    if (args.url or args.paste_file) and not args.yes:
        if input("\nLog this application? [Y/n] ").strip().lower() in ("n", "no"):
            raise SystemExit("Not logged.")

    row = sheets.append_job_row({
        "Applied Draes": args.date or _today_mdy(),
        "Position": title,
        "Company": company,
        "Required Skills & Experience": requirements,
        "location": location,
        "Job Link": link,
        "Status": status_mod.display(status_mod.APPLIED),
    })
    print(f"Logged to row {row}.")


def cmd_age_stale(args):
    stale.run(days=args.days, dry_run=not args.write)


def _since(args):
    return parse_date(args.since) if args.since else None


def cmd_list(args):
    since = _since(args)
    for r in sheets.list_rows():
        if since:
            applied = parse_date(r.get("Applied Draes", ""))
            if not applied or applied < since:
                continue
        print(f"[row {r['row_number']}] {r['Position']} @ {r['Company']} "
              f"| applied: {r.get('Applied Draes', '')} | status: {r['Status']}")


def cmd_update_status(args):
    # Through display() so a hand-typed "rejected" is stored the same way
    # every scanner stores it.
    sheets.update_row(args.row, {"Status": status_mod.display(args.status)})
    print(f"Row {args.row} status set to '{args.status}'.")


def cmd_scan_rejections(args):
    rejection_scanner.scan(days_back=args.days, dry_run=args.dry_run, since_date=_since(args))


def cmd_scan_job_links(args):
    link_finder.scan(days_back=args.days, dry_run=args.dry_run, since_date=_since(args))


def cmd_parser_accuracy(args):
    from sources import confirmation_corpus
    if args.refresh:
        confirmation_corpus.pull(days_back=args.days)
    confirmation_corpus.report(show=args.show)


def cmd_scan_new_applications(args):
    app_scanner.scan(days_back=args.days, dry_run=args.dry_run)


def cmd_color_rows(args):
    sheet_format.apply_status_colors(dry_run=args.dry_run)


def cmd_sort_by_date(args):
    sheets.sort_by_applied_date()
    print("Sheet sorted by applied date (oldest at top, newest at bottom).")


def cmd_auto(args):
    auto_pipeline.run(days_back=args.days, dry_run=args.dry_run)


def cmd_train_rejection_model(args):
    from sources import rejection_model
    rejection_model.train(days_back=args.days)


def cmd_open_loops(args):
    from outreach import open_loops
    open_loops.run(days_back=args.days)


def cmd_follow_ups(args):
    follow_up.scan(days_back=args.days, min_age_days=args.min_age)


def cmd_setup(args):
    from apply import setup_wizard
    setup_wizard.run(create=args.create_sheet, title=args.title)


def cmd_stage_drafts(args):
    from outreach import stage_drafts
    stage_drafts.run(dry_run=not args.write, create_drafts=args.drafts,
                     resume=args.company)


def cmd_schedule_followups(args):
    from outreach import followup_scheduler
    followup_scheduler.run(min_age_days=args.min_age, max_age_days=args.max_age,
                           max_drafts=args.max, dry_run=not args.draft)


def cmd_enable_calendar(args):
    from store import google_auth
    if google_auth.has_calendar_access():
        print("Calendar access is already granted.")
        return
    print("Opening a browser to grant read-only Google Calendar access.\n"
          "Approve it and this will be the last time you're asked.")
    google_auth.get_credentials(extra_scopes=[google_auth.CALENDAR_SCOPE])
    print("Granted." if google_auth.has_calendar_access() else "Not granted — try again.")


def cmd_autofill_extension(args):
    from apply import autofill_extension
    autofill_extension.run()


def cmd_autofill_link(args):
    from apply import autofill_bookmarklet
    autofill_bookmarklet.run()


def cmd_apply_profile(args):
    from apply import apply_autofill
    apply_autofill.run(company=args.company, role=args.role)


def cmd_auto_outreach(args):
    from outreach import auto_outreach
    auto_outreach.run(limit=args.limit, want=args.contacts, dry_run=args.dry_run)


def cmd_find_people(args):
    from people import people_search
    people_search.run(args.company, role=args.role, domain=args.domain,
                      state=args.state)


def cmd_recent_contacts(args):
    from people import recent_contacts
    recent_contacts.sweep(dry_run=args.dry_run, days=args.days)


def cmd_cover_letter(args):
    from apply import cover_letter
    made = cover_letter.build(args.body_file, args.company,
                              recipient=[r for r in (args.recipient or [])],
                              date=args.date)
    for path in made:
        print(f"  wrote {path}")
    if not made:
        print("Nothing written — check the body file exists and is not empty.")


def cmd_clean_positions(args):
    from sheetops import clean_positions
    clean_positions.run(dry_run=args.dry_run)


def cmd_address_for(args):
    from people import company_formats
    pattern, why = company_formats.pattern_for(args.domain)
    if not pattern:
        print(f"No address built for {args.domain}: {why}")
        print("Guessing here risks a bounce, which costs the domain's "
              "reputation for every later email.")
        return
    print(f"{company_formats.address_for(args.name, args.domain, pattern)}")
    print(f"  pattern {pattern} — {why}")


def cmd_interview_times(args):
    from store import gmail_client, interview_calendar
    threads = gmail_client.search_messages(
        f"newer_than:{args.days}d (interview OR \"speak\" OR \"call\")",
        include_body=True, max_results=args.limit)
    by_thread: dict = {}
    for msg in threads:
        by_thread.setdefault(msg.get("thread_id") or msg.get("id"), []).append(msg)
    found = 0
    for msgs in by_thread.values():
        agreed = interview_calendar.find_agreed_time(msgs)
        if not agreed:
            continue
        found += 1
        who = msgs[0].get("from", "")[:44]
        print(f"  {agreed}  {who}")
        if args.create:
            print("    ->", interview_calendar.create_event(
                agreed, summary=f"Interview — {who}", contact=who))
    print(f"\n{found} agreed time(s) found in the last {args.days} days."
          + ("" if args.create else "  Re-run with --create to add them."))


def cmd_prep(args):
    from apply import prep_package
    if not args.url and not args.text_file:
        raise SystemExit("give me either --url or --text-file")
    prep_package.generate(url=args.url, row_number=args.row, company=args.company,
                          role=args.role, cover_letter=args.cover_letter,
                          write_sheet=not args.no_sheet, text_file=args.text_file,
                          resume=not args.no_resume)


def cmd_backfill_job_descriptions(args):
    from sheetops import jd_backfill
    jd_backfill.run(dry_run=args.dry_run, limit=args.limit, days_back=args.days)


def cmd_find_jobs(args):
    from sources import job_finder
    areas = [args.area] if args.area else None
    job_finder.run(areas=areas, per_area=args.per_area,
                   skip_tracked_companies=args.new_companies_only)


def cmd_watch(args):
    from flows import watch
    watch.run(days_back=args.days, limit=args.limit, min_fit=args.min_fit,
              dry_run=args.dry_run, draft=not args.no_draft,
              deep=getattr(args, "deep", None))


def cmd_addresses(args):
    from people import address_book
    address_book.run(days_back=args.days, refresh=not args.cached,
                     address=args.check or "")


def cmd_auto_draft(args):
    from outreach import auto_draft
    auto_draft.run(limit=args.limit, min_fit=args.min_fit,
                   dry_run=not args.write_drafts,
                   create_drafts=args.write_drafts,
                   days_back=args.days, prefer=args.prefer,
                   exclude=args.exclude, new_only=not args.redo)


def cmd_batch_outreach(args):
    from outreach import batch_outreach
    batch_outreach.run(limit=args.limit, min_fit=args.min_fit)


def cmd_backfill_locations(args):
    from sheetops import location_backfill
    location_backfill.run(dry_run=args.dry_run, days_back=args.days,
                          use_email=not args.no_email)


def cmd_targets(args):
    from outreach import targets
    targets.run(days_back=args.days)


def cmd_style(args):
    from outreach import style_profile
    style_profile.run()


def cmd_edgar(args):
    from people import edgar_finder
    if args.form_d:
        edgar_finder.run_form_d(args.company)
    else:
        edgar_finder.run(args.company, since=args.since)


def cmd_sourcing(args):
    from people import sourcing
    sourcing.run(args.company, role=args.role or "", state=args.state)


def cmd_people(args):
    from people import people_finder
    people_finder.run(args.company, state=args.state, limit=args.limit,
                      show=args.show)


def cmd_findability(args):
    from people import findability
    if args.coverage:
        findability.run_coverage(days_back=args.days)
    else:
        findability.run(days_back=args.days, show=args.show)


def cmd_who_replies(args):
    from people import contact_policy
    contact_policy.analyse(days_back=args.days, limit=args.limit)


def cmd_corrections(args):
    from core import company_aliases
    from core import corrections
    if args.row:
        row = sheets.get_row(args.row)
        if not args.column or not args.value:
            raise SystemExit("pass --column and --value to lock a row")
        # Locks are keyed by (company, position), so correcting one of those
        # two has to be keyed by the identity the row will have *after* the
        # write. Keyed by the old name, a Company lock stopped matching its
        # own row the instant it was applied.
        company = args.value if args.column == "Company" else row["Company"]
        position = args.value if args.column == "Position" else row["Position"]
        corrections.record(company, position, args.column,
                           args.value, args.reason or "set by hand")
        if args.column == "Company":
            # Remembered so the ATS's own spelling still matches this row —
            # otherwise the next scan re-imports the application under it.
            company_aliases.record(row["Company"], args.value)
        sheets.update_row(args.row, {args.column: args.value})
        print(f"row {args.row} {args.column} = {args.value!r}, and locked "
              f"so no scanner will overwrite it.")
        return

    locks = corrections.all_locks()
    print(f"{len(locks)} application(s) with a locked field:\n")
    for entry in sorted(locks.values(), key=lambda e: e["company"].lower()):
        for column, lock in sorted(entry["locks"].items()):
            print(f"  {entry['company'][:26]:26s} | {entry['position'][:32]:32s} "
                  f"| {column} = {lock['value']}")
            if lock.get("reason"):
                print(f"      {lock['reason'][:96]}")


def cmd_dedupe(args):
    from sheetops import dedupe
    dedupe.run(dry_run=args.dry_run, max_day_gap=args.max_day_gap)


def cmd_fix_positions(args):
    from sheetops import fix_positions
    fix_positions.run(dry_run=args.dry_run, days_back=args.days)


def cmd_reoutreach(args):
    from outreach import reoutreach
    reoutreach.scan(days_back=args.days, min_silent_days=args.min_silent)


def cmd_scan_replies(args):
    from sources import reply_scanner
    reply_scanner.scan(days_back=args.days, dry_run=args.dry_run,
                       use_calendar=not args.no_calendar, notify=args.notify)


def cmd_train_interview_model(args):
    from sources import interview_model
    interview_model.train(days_back=args.days)


def cmd_cleanup_drafts(args):
    draft_cleanup.run(dry_run=args.dry_run)


def cmd_sync(args):
    print("=== 1/4: adding new applications found in Gmail ===")
    app_scanner.scan(days_back=args.days, dry_run=args.dry_run)
    print("\n=== 2/4: detecting rejections ===")
    rejection_scanner.scan(days_back=args.days, dry_run=args.dry_run)
    print("\n=== 3/4: sorting by applied date ===")
    if args.dry_run:
        print("(dry-run — skipped)")
    else:
        sheets.sort_by_applied_date()
        print("Sorted oldest-first.")
    print("\n=== 4/4: color-coding by status ===")
    sheet_format.apply_status_colors(dry_run=args.dry_run)


def cmd_export_past_outreach(args):
    query = f"in:sent {NETWORKING_KEYWORDS}"
    print(f"Searching Gmail: {query}\n")
    messages = gmail_client.search_messages(query, max_results=args.limit, include_body=True)
    print(f"Found {len(messages)} past outreach-looking email(s). Writing to {args.out}\n")
    with open(args.out, "w", encoding="utf-8") as f:
        for m in messages:
            f.write(f"=== {m['date']} | Subject: {m['subject']}\n")
            f.write(m["body"].strip() + "\n\n")
    print(f"Saved to {args.out} — reference this for tone/format when drafting new outreach emails.")


def cmd_build_outreach_corpus(args):
    from outreach import outreach_corpus
    outreach_corpus.build(max_results=args.limit)


def cmd_outreach_examples(args):
    from outreach import outreach_retrieval as orx
    row = sheets.get_row(args.row)
    query = orx.build_query(
        company=row.get("Company", ""),
        area=args.area or "",
        title=row.get("Contact Title", ""),
        role=row.get("Position", ""),
    )
    print(f"Query: {query}\n")
    idx = orx.load_index()
    hits = idx.search(query, k=args.k)
    if not hits:
        print("No similar past outreach emails found.")
        return
    for score, rec in hits:
        print("=" * 70)
        print(f"similarity {score:.3f} | {rec['date']} | to: {rec['to']}")
        print(f"Subject: {rec['subject']}")
        print("-" * 70)
        print(rec["body"].strip())
        print()


def cmd_find_contact_link(args):
    url = linkedin_helper.people_search_url(args.company, args.keywords)
    print("Open this in your browser (LinkedIn requires a logged-in human, not automation):")
    print(url)


def cmd_save_contact(args):
    sheets.update_row(args.row, {
        "Contact Name": args.name,
        "Contact Title": args.title or "",
        "Contact LinkedIn URL": args.linkedin_url or "",
    })
    print(f"Saved contact on row {args.row}.")


def cmd_draft_outreach_email(args):
    from people import recent_contacts
    when = recent_contacts.is_duplicate(args.to, args.subject)
    if when:
        print(f"Refusing: you already wrote to {args.to} on {when}.")
        print("A second introduction inside a year reads as generated, not "
              "meant. Send a follow-up on the original thread instead, or "
              "pick someone else on the desk.")
        return
    body = _read_body(args)
    gmail_client.create_draft(args.to, args.subject, body, attachments=args.attach)
    sheets.update_row(args.row, {"Outreach Email Status": "drafted"})
    print(f"Outreach draft created in Gmail and row {args.row} updated. Review and send from Gmail.")


def cmd_auto_draft_outreach(args):
    row = sheets.get_row(args.row)
    if args.name:
        # The sheet has no Contact Name column to read this from (trimmed to
        # A-J), so the generator would otherwise always greet with "Hello,"
        # instead of the person's actual name.
        row["Contact Name"] = args.name
    if args.follow_up:
        subject, body = email_drafter.generate_follow_up(row)
    else:
        subject, body = email_drafter.generate_outreach(row)

    if args.dry_run:
        print(f"Subject: {subject}\n\n{body}")
        return

    gmail_client.create_draft(args.to, subject, body, attachments=args.attach)
    sheets.update_row(args.row, {"Outreach Email Status": "drafted"})
    print(f"Draft created in Gmail for row {args.row} ({row.get('Company', '')}) -> {args.to}")
    print(f"Subject: {subject}\n\n{body}")


def cmd_draft_application_email(args):
    body = _read_body(args)
    gmail_client.create_draft(args.to, args.subject, body, attachments=args.attach)
    sheets.update_row(args.row, {"Application Email Status": "drafted"})
    print(f"Application draft created in Gmail and row {args.row} updated. Review and send from Gmail.")


def _read_body(args) -> str:
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            return f.read()
    return args.body


def _today_mdy() -> str:
    import datetime
    today = datetime.date.today()
    return f"{today.month}/{today.day}/{today.year}"


def cmd_network_audit(args):
    from people import network_audit
    network_audit.run(days_back=args.days, dry_run=args.dry_run)


def cmd_clean_sheet(args):
    from sheetops import sheet_clean
    sheet_clean.run(dry_run=args.dry_run)
