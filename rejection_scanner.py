"""Scans Gmail for rejection emails and matches them back to rows still
marked 'applied' in the sheet, updating Status to 'rejected' automatically.

Matching is deliberately conservative: a row is only auto-updated when
exactly one still-'applied' row's company name appears in the email. If
multiple rows share a company name, it's reported instead of guessed at.
"""

import gmail_client
import sheets
from date_utils import parse_date
from company_match import company_mentioned

REJECTION_PHRASES = [
    "unfortunately",
    "not moving forward",
    "will not be moving forward",
    "decided to move forward with other",
    "decided to proceed with other candidates",
    "other candidates",
    "not selected",
    "position has been filled",
    "pursue other candidates",
    "will not be proceeding",
]


def scan(days_back: int = 180, dry_run: bool = False, since_date=None) -> None:
    phrase_query = " OR ".join(f'"{p}"' for p in REJECTION_PHRASES)
    query = f"in:inbox newer_than:{days_back}d ({phrase_query})"

    print(f"Searching Gmail: {query}\n")
    messages = gmail_client.search_messages(query)
    print(f"Found {len(messages)} candidate rejection emails.\n")

    applied_rows = [
        r for r in sheets.list_rows()
        if r.get("Status", "").strip().lower() == "applied"
    ]
    if since_date:
        applied_rows = [
            r for r in applied_rows
            if (d := parse_date(r.get("Applied Draes", ""))) and d >= since_date
        ]
        print(f"Restricting to {len(applied_rows)} applied row(s) on/after {since_date}.\n")

    updated = []
    ambiguous = []
    unmatched = []

    for msg in messages:
        haystack = f"{msg['subject']} {msg['from']} {msg['snippet']}".lower()
        matches = [r for r in applied_rows if company_mentioned(r["Company"], haystack)]

        if len(matches) == 1:
            row = matches[0]
            if dry_run:
                updated.append((row, msg))
                continue
            note = f"Auto-detected rejection email ({msg['date']}): \"{msg['subject']}\""
            existing_notes = row.get("Notes", "")
            new_notes = f"{existing_notes}\n{note}".strip()
            sheets.update_row(row["row_number"], {"Status": "rejected", "Notes": new_notes})
            applied_rows.remove(row)  # don't match this row again this run
            updated.append((row, msg))
        elif len(matches) > 1:
            ambiguous.append((matches, msg))
        else:
            unmatched.append(msg)

    print(f"{'Would update' if dry_run else 'Updated'} {len(updated)} row(s):")
    for row, msg in updated:
        print(f"  row {row['row_number']}: {row['Position']} @ {row['Company']} <- \"{msg['subject']}\"")

    if ambiguous:
        print(f"\n{len(ambiguous)} email(s) matched multiple applied rows (skipped, review manually):")
        for matches, msg in ambiguous:
            rows_desc = ", ".join(f"row {r['row_number']}" for r in matches)
            print(f"  \"{msg['subject']}\" matched {rows_desc}")

    if unmatched:
        print(f"\n{len(unmatched)} rejection-looking email(s) didn't match any applied company (ignored):")
        for msg in unmatched:
            print(f"  \"{msg['subject']}\" from {msg['from']}")
