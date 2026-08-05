"""Fills the empty "Required Skills & Experience" column.

The outreach template's key paragraph reads this column, so an empty one makes
every generated email fall back to generic wording. It used to target a
separate "Job Description" column; the tracker was trimmed to A-J and that
content now lives here.

Two sources, best first:
  1. The Job Link, fetched and parsed like any posting.
  2. The application confirmation email, which sometimes restates the role.

Postings expire, so a dead link is expected and never fatal — the row just
falls through to the next source, and rows with no source at all are left
alone rather than filled with something invented. Expect a low hit rate: that
is the honest outcome, not a bug.
"""

import re

import gmail_client
import job_posting
import sheets
from company_match import company_mentioned, company_in_sender
from link_finder import CONFIRMATION_PHRASES

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")


# A posting URL that has expired usually redirects to the careers landing
# page, which parses fine and describes nothing ("Join our team! Please review
# the KBRA Privacy Statement"). Same rule as for email: no description beats a
# wrong one.
_LANDING_RE = re.compile(
    r"\b(current openings|join our team|all jobs|search jobs|privacy "
    r"statement|browse (our )?(jobs|openings)|no longer available|"
    r"job (has )?(expired|closed)|view all)\b", re.I)


_URLISH_RE = re.compile(r"https?://\S+")


def _from_link(url: str, company: str = "") -> str:
    parsed = job_posting.parse(job_posting.fetch(url, timeout=15))
    if parsed["thin"]:
        return ""
    summary = job_posting.summarize(parsed)
    if _LANDING_RE.search(summary) or len(summary) < 120:
        return ""
    # A summary that is mostly link text is the page's nav, not the job.
    if len(_URLISH_RE.sub("", summary)) < 100:
        return ""
    # Expired postings redirect, and LinkedIn happily serves a different
    # company's ad — a Brown Brothers Harriman listing landed on the Cody Peak
    # row. If the page never names the company, do not trust it.
    if company and not company_mentioned(company, summary.lower()):
        return ""
    return summary


# Courtesy wording that means the surrounding text is an acknowledgement, not
# a description of the job.
_COURTESY_RE = re.compile(
    r"\b(thank you for|we appreciate|in the meantime|follow us on|check the "
    r"status|we encourage you|takes time and energy|talent (team|acquisition)|"
    r"do not reply|unsubscribe|other roles|future opportunities)\b", re.I)

# Wording that actually describes work.
_DESCRIPTIVE_RE = re.compile(
    r"\b(you will|responsible for|responsibilities include|the role involves|"
    r"this role|reporting to|partner with|support the|manage|analy[sz]e|"
    r"develop|build|maintain|execute|research)\b", re.I)


def _from_email(row: dict, messages: list[dict]) -> str:
    """Summary drawn from this row's application confirmation email.

    Almost always returns nothing, on purpose. Confirmation mail is courtesy
    text — an early version happily wrote "the position. In the meantime, read
    more about Life at ICE" into seven rows. A Job Description that is
    boilerplate is worse than an empty one, because the outreach template
    reads this column and would quote it.
    """
    for msg in messages:
        haystack = f"{msg['subject']} {msg.get('snippet', '')}".lower()
        if not (company_mentioned(row["Company"], haystack)
                or company_in_sender(row["Company"], msg.get("from", ""))):
            continue
        body = _WS_RE.sub(" ", _TAG_RE.sub(" ", msg.get("body", "") or "")).strip()
        for marker in ("about the role", "responsibilities", "in this role you will",
                       "what you will do", "job description"):
            idx = body.lower().find(marker)
            if idx < 0:
                continue
            span = _WS_RE.sub(" ", body[idx:idx + 520]).strip()
            if len(span) < 140:
                continue
            if _COURTESY_RE.search(span) or not _DESCRIPTIVE_RE.search(span):
                continue
            return span
    return ""


TARGET_COLUMN = "Required Skills & Experience"


def run(dry_run: bool = False, limit: int = 60, days_back: int = 460) -> None:
    from config import HEADERS
    # The sheet was trimmed to A-J and "Job Description" no longer exists.
    # update_row drops unknown columns quietly, so without this check the
    # whole pass would report success while writing nothing at all.
    if TARGET_COLUMN not in HEADERS:
        raise SystemExit(
            f"the sheet has no {TARGET_COLUMN!r} column (headers: "
            f"{', '.join(HEADERS)}). Nothing to fill.")

    rows = sheets.list_rows()
    todo = [r for r in rows if not (r.get(TARGET_COLUMN) or "").strip()]
    print(f"{len(todo)} row(s) missing {TARGET_COLUMN} "
          f"(of {len(rows)}); processing up to {limit}.\n")

    query = f"newer_than:{days_back}d (" + \
            " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES) + ")"
    print("Reading application confirmation emails...")
    messages = gmail_client.search_messages(query, include_body=True, max_results=900)
    print(f"  {len(messages)} confirmation email(s) available as a fallback.\n")

    filled, sources = [], {"link": 0, "email": 0}
    for row in todo[:limit]:
        summary, source = "", ""

        url = (row.get("Job Link") or "").strip()
        if url.startswith("http"):
            try:
                summary = _from_link(url, row["Company"])
                source = "link" if summary else ""
            except Exception:
                summary = ""

        if not summary:
            summary = _from_email(row, messages)
            source = "email" if summary else ""

        # The Requirements column was tried as a third source and removed: in
        # this sheet it holds your own shorthand notes ("Interview two rounds",
        # "Applied in Gmail", "recruiting agency"), not the posting's
        # requirements, and copying it in filled 15 rows with nonsense.

        if not summary:
            continue

        sources[source] += 1
        filled.append((row, summary, source))
        print(f"  row {row['row_number']:3d} [{source:12s}] "
              f"{row['Position'][:34]:34s} @ {row['Company'][:22]}")
        print(f"        {summary[:110]}")

    print(f"\n{'Would fill' if dry_run else 'Filled'} {len(filled)} row(s) "
          f"— {sources['link']} from links, {sources['email']} from emails.")
    print("Most rows stay empty on purpose: postings expire and confirmation "
          "mail describes nothing.\nThe reliable filler is "
          "`cli.py prep --text-file <posting.txt> --row <N>`.")
    if dry_run:
        print("(dry-run — nothing written)")
        return

    for row, summary, _ in filled:
        sheets.update_row(row["row_number"], {TARGET_COLUMN: summary})
    print(f"Wrote {len(filled)} {TARGET_COLUMN} cell(s).")
