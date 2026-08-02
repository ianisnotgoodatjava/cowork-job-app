"""Scans Gmail for application-confirmation emails and auto-fills the
'Job Link' column for rows that don't have one yet, by extracting the job
posting URL out of the email body.

Same conservative matching as rejection_scanner: only auto-fills when
exactly one row (missing a Job Link) matches the email's company.
"""

import html
import re
import gmail_client
import sheets
from date_utils import parse_date
from company_match import company_mentioned

CONFIRMATION_PHRASES = [
    "thank you for applying",
    "thank you for your application",
    "application has been received",
    "your application to",
    "thanks for your interest",
    "application confirmation",
    "we have received your application",
    "your application was submitted",
    "thank you for your interest",
]

_URL_RE = re.compile(r"https?://[^\s\]\)<>\"']+")
_SKIP_URL_PATTERNS = ("unsubscribe", "privacy", "terms-of-service", "optout", "preferences", "urldefense.proofpoint.com", "linkedin.com/help")
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp")
_JOB_URL_HINTS = ("/job", "/jobs", "career", "position", "/apply", "requisition")

_TITLE_STOPWORDS = {
    "analyst", "associate", "senior", "junior", "the", "and", "for", "role",
    "position", "job", "id", "new", "york", "ny", "nyc", "chicago", "il",
    "boston", "ma", "remote", "at", "in", "of", "a", "an", "to", "with",
    "your", "you", "thank", "thanks", "applying", "application", "interest",
    "we", "have", "received", "been", "has", "was", "submitted", "re",
}


def _title_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _TITLE_STOPWORDS and len(w) > 2}


def _disambiguate(matches: list[dict], msg: dict) -> dict | None:
    """When multiple rows share a company, pick the one whose Position most
    distinctly overlaps the email subject (e.g. 'RMBS' vs 'CMBS' vs
    'Municipal Markets'). Returns None if no row is a clear unique best."""
    subject_tokens = _title_tokens(msg["subject"])
    if not subject_tokens:
        return None
    scored = sorted(
        ((len(_title_tokens(r["Position"]) & subject_tokens), r) for r in matches),
        key=lambda pair: -pair[0],
    )
    best_score = scored[0][0]
    if best_score > 0 and (len(scored) == 1 or scored[1][0] < best_score):
        return scored[0][1]
    return None


def _best_job_url(body: str) -> str | None:
    """Only returns a URL when it clearly looks like a job posting/apply
    link. Returns None (rather than guessing a random link) otherwise."""
    urls = [html.unescape(u.rstrip(".,;")) for u in _URL_RE.findall(body)]
    urls = [u for u in urls if not any(skip in u.lower() for skip in _SKIP_URL_PATTERNS)]
    urls = [u for u in urls if not u.lower().split("?")[0].endswith(_IMAGE_EXTENSIONS)]
    hinted = [u for u in urls if any(hint in u.lower() for hint in _JOB_URL_HINTS)]
    if hinted:
        return max(hinted, key=len)
    return None


def scan(days_back: int = 365, dry_run: bool = False, since_date=None) -> None:
    phrase_query = " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES)
    query = f"in:inbox newer_than:{days_back}d ({phrase_query})"

    print(f"Searching Gmail: {query}\n")
    messages = gmail_client.search_messages(query, include_body=True, max_results=200)
    print(f"Found {len(messages)} candidate confirmation emails.\n")

    target_rows = [r for r in sheets.list_rows() if not r.get("Job Link", "").strip()]
    if since_date:
        target_rows = [
            r for r in target_rows
            if (d := parse_date(r.get("Applied Draes", ""))) and d >= since_date
        ]
        print(f"Restricting to {len(target_rows)} row(s) missing a Job Link, applied on/after {since_date}.\n")
    else:
        print(f"{len(target_rows)} row(s) missing a Job Link.\n")

    updated = []
    ambiguous = []
    no_link_found = []

    for msg in messages:
        haystack = f"{msg['subject']} {msg['from']} {msg.get('body', '')}".lower()
        matches = [r for r in target_rows if company_mentioned(r["Company"], haystack)]

        row = None
        if len(matches) == 1:
            row = matches[0]
        elif len(matches) > 1:
            row = _disambiguate(matches, msg)
            if row is None:
                ambiguous.append((matches, msg))
                continue

        if row is None:
            continue

        url = _best_job_url(msg.get("body", ""))
        if not url:
            no_link_found.append((row, msg))
            continue

        if dry_run:
            updated.append((row, msg, url))
            continue

        sheets.update_row(row["row_number"], {"Job Link": url})
        target_rows.remove(row)
        updated.append((row, msg, url))

    print(f"{'Would fill' if dry_run else 'Filled'} Job Link on {len(updated)} row(s):")
    for row, msg, url in updated:
        print(f"  row {row['row_number']}: {row['Position']} @ {row['Company']} -> {url}")

    if ambiguous:
        print(f"\n{len(ambiguous)} email(s) matched multiple rows missing a link (skipped):")
        for matches, msg in ambiguous:
            rows_desc = ", ".join(f"row {r['row_number']}" for r in matches)
            print(f"  \"{msg['subject']}\" matched {rows_desc}")

    if no_link_found:
        print(f"\n{len(no_link_found)} matched email(s) had no extractable job link:")
        for row, msg in no_link_found:
            print(f"  row {row['row_number']} ({row['Company']}): \"{msg['subject']}\"")
