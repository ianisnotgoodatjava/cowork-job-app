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
    # SuccessFactors' subject line, which announces the application rather
    # than thanking you for it: "New Application Received  Fund Financing
    # Analyst/Associate Global Markets United States (US)". Nothing else in
    # this list matches that wording, so five Nomura applications were never
    # even returned by the search.
    "new application received",
    "your application to",
    "thanks for your interest",
    "application confirmation",
    "we have received your application",
    "your application was submitted",
    # LinkedIn's own confirmation ("Ian, your application was sent to X"),
    # distinct from the employer's ATS confirmation. Missing this meant every
    # LinkedIn "Easy Apply" went unscanned unless the employer separately
    # confirmed too — eight applications over 30 days, silently absent.
    "your application was sent to",
    "thank you for your interest",
]

_URL_RE = re.compile(r"https?://[^\s\]\)<>\"']+")
_SKIP_URL_PATTERNS = ("unsubscribe", "privacy", "terms-of-service", "optout",
                      "preferences", "linkedin.com/help",
                      # CDN assets. A Workday email embeds its logo from
                      # myworkdaycdn.com/wday/image/..., which matched the
                      # Workday domain hint and was stored as the posting.
                      "myworkdaycdn.com", "/wday/image/", "/wday/asset")
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp")
_JOB_URL_HINTS = ("/job", "/jobs", "career", "position", "/apply", "requisition",
                  "/opening", "/vacancy", "/posting", "/req")

# The ATS a posting is hosted on. Path hints alone missed almost everything:
# a Workday posting lives at ".../myworkdayjobs.com/en-US/External/details/..."
# which contains none of the path fragments above, and Workday is the single
# most common host in this mailbox.
_ATS_DOMAIN_HINTS = (
    "myworkdayjobs.com", "myworkdaysite.com", "wd1.myworkday", "wd3.myworkday",
    "wd5.myworkday", "icims.com", "greenhouse.io", "lever.co", "ashbyhq.com",
    "taleo.net", "smartrecruiters.com", "successfactors.com", "jobvite.com",
    "workable.com", "bamboohr.com", "breezy.hr", "recruitee.com",
    "applytojob.com", "ultipro.com", "dayforcehcm.com", "paylocity.com",
    "brassring.com", "avature.net", "eightfold.ai", "phenompeople.com",
    "oraclecloud.com/hcmUI", "silkroad.com", "jazzhr.com", "rippling.com",
)

# A posting URL almost always carries the requisition id. Preferring one that
# does is what separates the posting itself from the careers landing page the
# same email links to.
_REQ_IN_URL_RE = re.compile(r"(?:/|_|-|=)(?:R-?)?\d{4,}\b|/details/|/job/", re.I)

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
    distinctly overlaps the email (e.g. 'RMBS' vs 'CMBS' vs 'Municipal
    Markets'). Returns None if no row is a clear unique best.

    The company's own name is removed from both sides first: a row whose
    Position happens to embed the company ("Funds Ratings ... at KBRA") would
    otherwise beat its siblings on a generic subject like "Thank you for your
    interest in KBRA!", which carries no role information at all.
    """
    company_tokens = _title_tokens(matches[0].get("Company", ""))
    # The body usually names the exact role even when the subject does not.
    haystack = msg["subject"] + " " + (msg.get("body") or "")[:4000]
    if "<" in haystack:
        haystack = html.unescape(re.sub(r"<[^>]+>", " ", haystack))
    subject_tokens = _title_tokens(haystack) - company_tokens
    if not subject_tokens:
        return None
    scored = sorted(
        (((len((_title_tokens(r["Position"]) - company_tokens) & subject_tokens)), r) for r in matches),
        key=lambda pair: -pair[0],
    )
    best_score = scored[0][0]
    if best_score > 0 and (len(scored) == 1 or scored[1][0] < best_score):
        return scored[0][1]

    # Exact tokens tied. Recruiters abbreviate ("quant sales assistant" for the
    # Quantitative Sales Assistant row), so break the tie on tokens the email
    # abbreviates — this is what separated Wolfe's near-identical
    # "Equity Sales Assistant" and "Quantitative Sales Assistant" rows.
    if best_score > 0:
        tied = [r for score, r in scored if score == best_score]
        reranked = sorted(
            ((_prefix_overlap(_title_tokens(r["Position"]) - company_tokens,
                              subject_tokens), r) for r in tied),
            key=lambda pair: -pair[0],
        )
        if reranked[0][0] > 0 and (len(reranked) == 1 or reranked[1][0] < reranked[0][0]):
            return reranked[0][1]
    return None


def _prefix_overlap(row_tokens: set[str], email_tokens: set[str]) -> int:
    """Row tokens the email refers to by a shortened form ('quant' -> 'quantitative')."""
    count = 0
    for row_token in row_tokens:
        for email_token in email_tokens:
            if email_token != row_token and len(email_token) >= 4 \
                    and row_token.startswith(email_token):
                count += 1
                break
    return count


def _role_conflicts(row: dict, msg: dict) -> bool:
    """True when the email clearly names a role other than this row's.

    Used to reject a company-only match. Returns False when the email says
    nothing role-specific, so generic emails still match a lone open row.
    """
    company_tokens = _title_tokens(row.get("Company", ""))
    text = msg["subject"] + " " + (msg.get("body") or "")[:4000]
    if "<" in text:
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    email_tokens = _title_tokens(text) - company_tokens
    role_tokens = _title_tokens(row.get("Position", "")) - company_tokens
    if not role_tokens or not email_tokens:
        return False
    # Only conflict when the email is specific enough to be talking about a
    # named role and none of this row's distinctive words appear in it.
    return not (role_tokens & email_tokens)


# Proofpoint rewrites every link in mail that passes through it, and this
# mailbox gets a lot of it. The wrapper was on the skip list, so those emails
# looked like they contained no job link at all — the real URL is sitting
# inside the u= parameter, encoded with "_" for "/" and "-XX" for a literal
# byte. Unwrapping it is the difference between 38 links and several hundred.
_PROOFPOINT_V2_RE = re.compile(
    r"urldefense\.proofpoint\.com/v2/url\?u=([^&\s]+)", re.I)
_PROOFPOINT_V3_RE = re.compile(
    r"urldefense(?:\.proofpoint)?\.com/v3/__(.+?)__;", re.I)


_SAFELINKS_RE = re.compile(
    r"safelinks\.protection\.outlook\.com/\?url=([^&\s]+)", re.I)

# LinkedIn appends several hundred bytes of tracking to every job link. The
# posting is identified entirely by its id, so the rest is noise in a cell you
# have to read.
_LINKEDIN_JOB_RE = re.compile(
    r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.I)


def _unwrap(url: str) -> str:
    """Recovers the real destination from a mail-gateway rewrite."""
    m = _SAFELINKS_RE.search(url)
    if m:
        from urllib.parse import unquote
        url = unquote(m.group(1))
    m = _PROOFPOINT_V3_RE.search(url)
    if m:
        return _canonical(html.unescape(m.group(1)))
    m = _PROOFPOINT_V2_RE.search(url)
    if not m:
        return _canonical(url)
    raw = m.group(1).replace("_", "/")
    raw = re.sub(r"-([0-9A-Fa-f]{2})",
                 lambda h: chr(int(h.group(1), 16)), raw)
    return _canonical(html.unescape(raw))


def _canonical(url: str) -> str:
    """Strips tracking noise that is not part of the posting's identity."""
    m = _LINKEDIN_JOB_RE.search(url)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}"
    return url


def _best_job_url(body: str) -> str | None:
    """Only returns a URL when it clearly looks like a job posting/apply
    link. Returns None (rather than guessing a random link) otherwise."""
    urls = [_unwrap(html.unescape(u.rstrip(".,;")))
            for u in _URL_RE.findall(body)]
    urls = [u for u in urls if not any(skip in u.lower() for skip in _SKIP_URL_PATTERNS)]
    urls = [u for u in urls if not u.lower().split("?")[0].endswith(_IMAGE_EXTENSIONS)]
    hinted = [u for u in urls
              if any(h in u.lower() for h in _JOB_URL_HINTS)
              or any(h in u.lower() for h in _ATS_DOMAIN_HINTS)]
    if not hinted:
        return None
    # Rank rather than taking the longest: the longest URL in an ATS email is
    # usually the tracking wrapper, not the posting.
    def score(pair) -> tuple:
        i, u = pair
        low = u.lower()
        return (
            bool(_REQ_IN_URL_RE.search(u)),
            any(h in low for h in _ATS_DOMAIN_HINTS),
            any(h in low for h in _JOB_URL_HINTS),
            -i,
        )
    return max(enumerate(hinted), key=score)[1]


def scan(days_back: int = 365, dry_run: bool = False, since_date=None) -> None:
    phrase_query = " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES)
    # No "in:inbox": confirmations get archived, and those rows need a link
    # just as much. max_results was 200, which silently capped the scan at a
    # fraction of the mail that matched.
    query = f"newer_than:{days_back}d -in:chats ({phrase_query})"

    print(f"Searching Gmail: {query[:120]}...\n")
    messages = gmail_client.search_messages(query, include_body=True,
                                            max_results=3000)
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
    pending: dict[int, dict] = {}

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

        # Collected, not written here. Writing per row hit the Sheets quota of
        # 60 writes per minute part-way through a full scan and aborted it.
        pending[row["row_number"]] = {"Job Link": url}
        target_rows.remove(row)
        updated.append((row, msg, url))

    if pending:
        sheets.update_rows(pending)

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
