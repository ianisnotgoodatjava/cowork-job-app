"""Command-line entry point for the job-application tracker.

Typical flow:
  1. python cli.py log-application --job-title ... --company ... --requirements ... --location ... --link ...
  2. python cli.py find-contact-link --company "Acme Corp"      # opens manually in your browser
  3. python cli.py save-contact --row 2 --name "Jane Doe" --title "Engineering Manager" --linkedin-url "..."
  4. python cli.py draft-outreach-email --row 2 --to jane@acme.com --subject "..." --body-file outreach.txt
  5. python cli.py draft-application-email --row 2 --to jobs@acme.com --subject "..." --body-file cover_letter.txt --attach resume.pdf
"""

import argparse
from dotenv import load_dotenv

load_dotenv()

import sheets
import gmail_client
import linkedin_helper
import rejection_scanner
import link_finder
import app_scanner
import auto_pipeline
import draft_cleanup
import follow_up
import sheet_format
import stale
from date_utils import parse_date
import email_drafter


def cmd_log_application(args):
    import apply_capture
    from company_match import _core

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
        "Status": "applied",
    })
    print(f"Logged to row {row}.")


def cmd_age_stale(args):
    stale.run(days=args.days, dry_run=not args.write)


def cmd_list(args):
    since = parse_date(args.since) if args.since else None
    for r in sheets.list_rows():
        if since:
            applied = parse_date(r.get("Applied Draes", ""))
            if not applied or applied < since:
                continue
        print(f"[row {r['row_number']}] {r['Position']} @ {r['Company']} "
              f"| applied: {r.get('Applied Draes', '')} | status: {r['Status']} "
              f"| outreach: {r['Outreach Email Status']} | application email: {r['Application Email Status']}")


def cmd_update_status(args):
    sheets.update_row(args.row, {"Status": args.status})
    print(f"Row {args.row} status set to '{args.status}'.")


def cmd_scan_rejections(args):
    since = parse_date(args.since) if args.since else None
    rejection_scanner.scan(days_back=args.days, dry_run=args.dry_run, since_date=since)


def cmd_scan_job_links(args):
    since = parse_date(args.since) if args.since else None
    link_finder.scan(days_back=args.days, dry_run=args.dry_run, since_date=since)


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
    import rejection_model
    rejection_model.train(days_back=args.days)


def cmd_follow_ups(args):
    follow_up.scan(days_back=args.days, min_age_days=args.min_age)


def cmd_enable_calendar(args):
    import google_auth
    if google_auth.has_calendar_access():
        print("Calendar access is already granted.")
        return
    print("Opening a browser to grant read-only Google Calendar access.\n"
          "Approve it and this will be the last time you're asked.")
    google_auth.get_credentials(extra_scopes=[google_auth.CALENDAR_SCOPE])
    print("Granted." if google_auth.has_calendar_access() else "Not granted — try again.")


def cmd_prep(args):
    import prep_package
    if not args.url and not args.text_file:
        raise SystemExit("give me either --url or --text-file")
    prep_package.generate(url=args.url, row_number=args.row, company=args.company,
                          role=args.role, cover_letter=args.cover_letter,
                          write_sheet=not args.no_sheet, text_file=args.text_file,
                          resume=not args.no_resume)


def cmd_backfill_job_descriptions(args):
    import jd_backfill
    jd_backfill.run(dry_run=args.dry_run, limit=args.limit, days_back=args.days)


def cmd_corrections(args):
    import corrections
    if args.row:
        row = sheets.get_row(args.row)
        if not args.column or not args.value:
            raise SystemExit("pass --column and --value to lock a row")
        corrections.record(row["Company"], row["Position"], args.column,
                           args.value, args.reason or "set by hand")
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
    import dedupe
    dedupe.run(dry_run=args.dry_run, max_day_gap=args.max_day_gap)


def cmd_fix_positions(args):
    import fix_positions
    fix_positions.run(dry_run=args.dry_run, days_back=args.days)


def cmd_reoutreach(args):
    import reoutreach
    reoutreach.scan(days_back=args.days, min_silent_days=args.min_silent)


def cmd_scan_replies(args):
    import reply_scanner
    reply_scanner.scan(days_back=args.days, dry_run=args.dry_run,
                       use_calendar=not args.no_calendar, notify=args.notify)


def cmd_train_interview_model(args):
    import interview_model
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


NETWORKING_KEYWORDS = (
    '("reaching out" OR "quick question" OR "informational" OR "coffee chat" '
    'OR "would love to learn" OR "connect" OR "your experience" OR "quick call" '
    'OR "picking your brain" OR referral)'
)


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
    import outreach_corpus
    outreach_corpus.build(max_results=args.limit)


def cmd_outreach_examples(args):
    import outreach_retrieval as orx
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
    body = _read_body(args)
    gmail_client.create_draft(args.to, args.subject, body, attachments=args.attach)
    sheets.update_row(args.row, {"Outreach Email Status": "drafted"})
    print(f"Outreach draft created in Gmail and row {args.row} updated. Review and send from Gmail.")


def cmd_auto_draft_outreach(args):
    row = sheets.get_row(args.row)
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


def main():
    parser = argparse.ArgumentParser(description="Job application tracker CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("log-application", help="Log a new job application to the sheet")
    p.add_argument("--url", help="Job posting URL — reads the live posting and "
                                 "fills title, company, location and requirements")
    p.add_argument("--paste-file", help="File holding the posting text you copied "
                                        "from the page. Use for Workday/LinkedIn, "
                                        "which won't serve the posting to a script")
    p.add_argument("--job-title")
    p.add_argument("--company")
    p.add_argument("--requirements")
    p.add_argument("--location")
    p.add_argument("--link")
    p.add_argument("--date", help="Defaults to today (YYYY-MM-DD)")
    p.add_argument("--yes", action="store_true",
                   help="Skip the confirmation prompt")
    p.set_defaults(func=cmd_log_application)

    p = sub.add_parser("age-stale", help="Move applications that went silent past N days from 'applied' to 'no response'")
    p.add_argument("--days", type=int, default=stale.DEFAULT_DAYS,
                   help=f"Silence threshold in days (default {stale.DEFAULT_DAYS})")
    p.add_argument("--write", action="store_true",
                   help="Apply the changes (default is a dry run)")
    p.set_defaults(func=cmd_age_stale)

    p = sub.add_parser("list", help="List logged applications")
    p.add_argument("--since", help="Only show applications applied on/after this date (YYYY-MM-DD)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("update-status", help="Update the Status column (applied/rejected/interview/offer)")
    p.add_argument("--row", type=int, required=True)
    p.add_argument("--status", required=True, choices=["applied", "interviewing", "interview-rejected", "rejected", "offer"])
    p.set_defaults(func=cmd_update_status)

    p = sub.add_parser("scan-rejections", help="Scan Gmail for rejection emails and auto-update matching rows")
    p.add_argument("--days", type=int, default=180, help="How many days back to search (default 180)")
    p.add_argument("--dry-run", action="store_true", help="Show what would change without writing to the sheet")
    p.add_argument("--since", help="Only consider applications applied on/after this date (YYYY-MM-DD)")
    p.set_defaults(func=cmd_scan_rejections)

    p = sub.add_parser("enable-calendar", help="Grant read-only Google Calendar access (one-time browser consent)")
    p.set_defaults(func=cmd_enable_calendar)

    p = sub.add_parser("prep", help="Build an application prep package (bullets, ATS answers, parsed requirements) from a job URL")
    p.add_argument("--url", default="", help="Job posting URL")
    p.add_argument("--text-file", default="", help="Posting text you pasted into a file (use when the ATS blocks fetching)")
    p.add_argument("--row", type=int, help="Sheet row this application is on (also writes Job Description/Requirements)")
    p.add_argument("--company", default="", help="Override company name")
    p.add_argument("--role", default="", help="Override role title")
    p.add_argument("--cover-letter", action="store_true", help="Also draft a cover letter")
    p.add_argument("--no-resume", action="store_true", help="Skip the tailored PDF resume")
    p.add_argument("--no-sheet", action="store_true", help="Don't write anything to the sheet")
    p.set_defaults(func=cmd_prep)

    p = sub.add_parser("backfill-job-descriptions", help="Fill the empty Job Description column from job links and application emails")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=60, help="Max rows to fill in one pass (default 60)")
    p.add_argument("--days", type=int, default=460, help="How far back to read application emails")
    p.set_defaults(func=cmd_backfill_job_descriptions)

    p = sub.add_parser("corrections", help="Show fields you've corrected (scanners never overwrite these); with --row, set and lock one")
    p.add_argument("--row", type=int, help="Row to correct and lock")
    p.add_argument("--column", help="Column to lock, e.g. Status")
    p.add_argument("--value", help="Value to set")
    p.add_argument("--reason", default="", help="Why — recorded alongside the lock")
    p.set_defaults(func=cmd_corrections)

    p = sub.add_parser("dedupe", help="Find rows that are the same application logged twice (fuzzy company/title/date match)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-day-gap", type=int, default=21, help="Treat as the same application if applied within this many days (default 21)")
    p.set_defaults(func=cmd_dedupe)

    p = sub.add_parser("fix-positions", help="Repair imported rows stuck on the placeholder role 'Analyst', and drop ones that duplicate your own rows")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--days", type=int, default=520)
    p.set_defaults(func=cmd_fix_positions)

    p = sub.add_parser("reach-out-again", help="Warm leads that went cold: someone replied, then it went quiet with no rejection")
    p.add_argument("--days", type=int, default=520, help="How far back to look (default 520)")
    p.add_argument("--min-silent", type=int, default=21, help="Only show threads quiet this many days (default 21)")
    p.set_defaults(func=cmd_reoutreach)

    p = sub.add_parser("scan-replies", help="Detect interview invites and real human replies; set Status=interviewing and Network=Yes")
    p.add_argument("--days", type=int, default=460, help="How many days back to search (default 460)")
    p.add_argument("--dry-run", action="store_true", help="Show what would change without writing to the sheet")
    p.add_argument("--no-calendar", action="store_true", help="Skip Google Calendar evidence")
    p.add_argument("--notify", action="store_true", help="Email yourself a reminder about new reach-outs")
    p.set_defaults(func=cmd_scan_replies)

    p = sub.add_parser("train-interview-model", help="Retrain the post-interview-rejection classifier (labelled from your calendar)")
    p.add_argument("--days", type=int, default=500, help="How many days back to train on (default 500)")
    p.set_defaults(func=cmd_train_interview_model)

    p = sub.add_parser("scan-job-links", help="Scan Gmail confirmation emails to auto-fill missing Job Link column")
    p.add_argument("--days", type=int, default=365, help="How many days back to search (default 365)")
    p.add_argument("--dry-run", action="store_true", help="Show what would change without writing to the sheet")
    p.add_argument("--since", help="Only consider applications applied on/after this date (YYYY-MM-DD)")
    p.set_defaults(func=cmd_scan_job_links)

    p = sub.add_parser("auto", help="Full automated pass: add new applications, detect rejections, sort, color, and report rows ready for outreach")
    p.add_argument("--days", type=int, default=30, help="Look back this many days (default 30)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("train-rejection-model", help="Retrain the learned rejection classifier from your mail")
    p.add_argument("--days", type=int, default=500)
    p.set_defaults(func=cmd_train_rejection_model)

    p = sub.add_parser("follow-ups", help="List outreach emails that never got a reply")
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--min-age", type=int, default=7, help="Only flag sends older than this many days")
    p.set_defaults(func=cmd_follow_ups)

    p = sub.add_parser("cleanup-drafts", help="Delete outreach drafts for applications that were rejected")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_cleanup_drafts)

    p = sub.add_parser("sync", help="Full sync: add new applications from Gmail, detect rejections, sort by date, color-code")
    p.add_argument("--days", type=int, default=730, help="How many days back to search (default 730)")
    p.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("scan-new-applications", help="Find applications in Gmail that aren't in the sheet yet and add them")
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_scan_new_applications)

    p = sub.add_parser("color-rows", help="Color-code rows: red = rejected, green = interview/offer")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_color_rows)

    p = sub.add_parser("sort-by-date", help="Sort the sheet by applied date, oldest at top")
    p.set_defaults(func=cmd_sort_by_date)

    p = sub.add_parser("export-past-outreach", help="Save your past sent networking emails to a file, for tone/format reference")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--out", default="past_outreach.txt")
    p.set_defaults(func=cmd_export_past_outreach)

    p = sub.add_parser("build-outreach-corpus", help="Index your past sent outreach emails for retrieval")
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(func=cmd_build_outreach_corpus)

    p = sub.add_parser("outreach-examples", help="Show your most similar past outreach emails for a row, to base a new draft on")
    p.add_argument("--row", type=int, required=True)
    p.add_argument("--area", help="Team/subject area to weight the search (e.g. 'RMBS')")
    p.add_argument("-k", type=int, default=3, help="How many examples to show")
    p.set_defaults(func=cmd_outreach_examples)

    p = sub.add_parser("find-contact-link", help="Print a LinkedIn people-search URL to browse manually")
    p.add_argument("--company", required=True)
    p.add_argument("--keywords", default="recruiter OR talent OR hiring")
    p.set_defaults(func=cmd_find_contact_link)

    p = sub.add_parser("save-contact", help="Save a contact you found manually to a row")
    p.add_argument("--row", type=int, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--title")
    p.add_argument("--linkedin-url")
    p.set_defaults(func=cmd_save_contact)

    p = sub.add_parser("draft-outreach-email", help="Create a Gmail draft to the contact on a row")
    p.add_argument("--row", type=int, required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--attach", action="append", default=[], help="Path to attach (e.g. resume.pdf), repeatable")
    p.set_defaults(func=cmd_draft_outreach_email)

    p = sub.add_parser("auto-draft-outreach", help="Auto-generate an outreach email from a row, matching your past networking tone, and create it as a Gmail draft")
    p.add_argument("--row", type=int, required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--follow-up", action="store_true", help="Generate a follow-up instead of the initial outreach")
    p.add_argument("--attach", action="append", default=[], help="Path to attach (e.g. resume.pdf), repeatable")
    p.add_argument("--dry-run", action="store_true", help="Print the generated email without creating a Gmail draft")
    p.set_defaults(func=cmd_auto_draft_outreach)

    p = sub.add_parser("draft-application-email", help="Create a Gmail draft to apply via email")
    p.add_argument("--row", type=int, required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--attach", action="append", default=[], help="Path to attach, repeatable")
    p.set_defaults(func=cmd_draft_application_email)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
