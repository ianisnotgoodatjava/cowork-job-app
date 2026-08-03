"""Scans Gmail for rejection emails and matches them back to rows still
marked 'applied' in the sheet, updating Status to 'rejected' automatically.

Matching is deliberately conservative: a row is only auto-updated when
exactly one still-'applied' row's company name appears in the email. If
multiple rows share a company name, it's reported instead of guessed at.
"""

import html
import re

import gmail_client
import sheets
from date_utils import parse_date
from company_match import company_mentioned
from link_finder import _disambiguate, _role_conflicts

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
    "is now closed",
    "no longer under consideration",
    "not be moving forward",
    "moving forward with other",
    "decided not to move forward",
    "filled this position",
    "position is closed",
]

# Gmail's search only tells us a phrase appears *somewhere*; confirmation
# emails routinely contain rejection-sounding boilerplate ("if you are not
# selected..."). These are the phrases that only appear in a real rejection,
# and one of them must be present in the body before a row is marked.
DECISIVE_PHRASES = [
    "not moving forward",
    "will not be moving forward",
    "not be moving forward",
    "decided to move forward with other",
    "moving forward with other",
    "decided to proceed with other candidates",
    "pursue other candidates",
    "not selected",
    "position has been filled",
    "filled this position",
    "position is closed",
    "is now closed",
    "no longer under consideration",
    "will not be proceeding",
    "decided not to move forward",
    "not to move forward with your",
    "other candidates whose",
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _plain_text(body: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", body or ""))).lower()


# Confirmation emails describe rejection hypothetically ("if you are not
# selected at this stage, we will keep your application on file"). A decisive
# phrase in that context is boilerplate, not a decision about this candidate.
_CONDITIONAL_LEAD_RE = re.compile(
    r"(if|should|unless|whether|in case|in the event)\b[^.]{0,60}$", re.I
)


def _is_real_rejection(msg: dict) -> str | None:
    """Returns the decisive phrase found in the body, or None."""
    text = _plain_text(msg.get("body", ""))
    for phrase in DECISIVE_PHRASES:
        idx = text.find(phrase)
        while idx >= 0:
            lead = text[max(0, idx - 60):idx]
            if not _CONDITIONAL_LEAD_RE.search(lead):
                return phrase
            idx = text.find(phrase, idx + 1)
    return None


def scan(days_back: int = 180, dry_run: bool = False, since_date=None) -> None:
    phrase_query = " OR ".join(f'"{p}"' for p in REJECTION_PHRASES)
    query = f"in:inbox newer_than:{days_back}d ({phrase_query})"

    print(f"Searching Gmail: {query}\n")
    messages = gmail_client.search_messages(query, include_body=True)
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
    not_rejections = []

    for msg in messages:
        # 'from' is deliberately excluded: ATS vendor domains (myworkday.com,
        # oraclecloud.com, icims.com) made unrelated companies match.
        phrase = _is_real_rejection(msg)
        if not phrase:
            not_rejections.append(msg)
            continue
        haystack = f"{msg['subject']} {msg['snippet']}".lower()
        matches = [r for r in applied_rows if company_mentioned(r["Company"], haystack)]
        # Several rows at one company (e.g. two Salesforce applications) used
        # to be skipped outright; the email's subject usually names which role
        # it is about, so try that before giving up.
        if len(matches) > 1:
            resolved = _disambiguate(matches, msg)
            if resolved is not None:
                matches = [resolved]

        # A single remaining match is not proof: if the row this email is really
        # about was already marked rejected, the only 'applied' row left at
        # that company will match by company name alone. Require that the
        # email does not name a clearly different role.
        if len(matches) == 1 and _role_conflicts(matches[0], msg):
            ambiguous.append((matches, msg))
            continue

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

    if not_rejections:
        print(f"\n{len(not_rejections)} email(s) matched the search but had no decisive "
              f"rejection wording in the body (ignored — most are confirmations).")

    if unmatched:
        print(f"\n{len(unmatched)} rejection email(s) didn't match any applied company (ignored):")
        for msg in unmatched:
            print(f"  \"{msg['subject']}\" from {msg['from']}")
