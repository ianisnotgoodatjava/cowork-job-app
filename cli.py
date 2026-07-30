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
from date_utils import parse_date


def cmd_log_application(args):
    row = sheets.append_job_row({
        "Applied Draes": args.date or _today_mdy(),
        "Position": args.job_title,
        "Company": args.company,
        "Requirements": args.requirements or "",
        "location": args.location or "",
        "Job Link": args.link or "",
        "Status": "applied",
        "Application Email Status": "not needed",
        "Outreach Email Status": "not started",
    })
    print(f"Logged to row {row}.")


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
    gmail_client.create_draft(args.to, args.subject, body)
    sheets.update_row(args.row, {"Outreach Email Status": "drafted"})
    print(f"Outreach draft created in Gmail and row {args.row} updated. Review and send from Gmail.")


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
    p.add_argument("--job-title", required=True)
    p.add_argument("--company", required=True)
    p.add_argument("--requirements")
    p.add_argument("--location")
    p.add_argument("--link")
    p.add_argument("--date", help="Defaults to today (YYYY-MM-DD)")
    p.set_defaults(func=cmd_log_application)

    p = sub.add_parser("list", help="List logged applications")
    p.add_argument("--since", help="Only show applications applied on/after this date (YYYY-MM-DD)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("update-status", help="Update the Status column (applied/rejected/interview/offer)")
    p.add_argument("--row", type=int, required=True)
    p.add_argument("--status", required=True, choices=["applied", "rejected", "interview", "offer"])
    p.set_defaults(func=cmd_update_status)

    p = sub.add_parser("scan-rejections", help="Scan Gmail for rejection emails and auto-update matching rows")
    p.add_argument("--days", type=int, default=180, help="How many days back to search (default 180)")
    p.add_argument("--dry-run", action="store_true", help="Show what would change without writing to the sheet")
    p.add_argument("--since", help="Only consider applications applied on/after this date (YYYY-MM-DD)")
    p.set_defaults(func=cmd_scan_rejections)

    p = sub.add_parser("export-past-outreach", help="Save your past sent networking emails to a file, for tone/format reference")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--out", default="past_outreach.txt")
    p.set_defaults(func=cmd_export_past_outreach)

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
    p.set_defaults(func=cmd_draft_outreach_email)

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
