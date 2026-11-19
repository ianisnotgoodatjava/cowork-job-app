"""Fetches a job posting and pulls out the parts the sheet and the prep pack need.

Deliberately plain: one GET with a browser-ish User-Agent, then HTML parsing.
No headless browser, no login, no clicking — this only reads a page you could
open yourself, which keeps it clear of bot-detection and ToS trouble. Postings
behind a JS-only app (some Workday tenants) will come back thin; the caller is
told so rather than being handed a confident-looking empty parse.
"""

import re
import textwrap

import requests
from bs4 import BeautifulSoup

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Headings that introduce the list you actually want.
_REQ_HEADING_RE = re.compile(
    r"\b(requirements?|qualifications?|what you.{0,15}(need|bring)|"
    r"who you are|skills|experience required|basic qualifications|"
    r"minimum qualifications|preferred qualifications|we.{0,5}re looking for)\b",
    re.I,
)
_RESP_HEADING_RE = re.compile(
    r"\b(responsibilities|what you.{0,10}ll do|the role|about the (role|job|position)|"
    r"day to day|duties|your impact)\b",
    re.I,
)

_BOILERPLATE_RE = re.compile(
    r"\b(equal opportunity|eeo|affirmative action|reasonable accommodation|"
    r"privacy policy|cookie|drug[- ]free|background check|e-verify)\b", re.I,
)


# Faceted-search chrome: counts in parentheses, "Done"/"Clear text" controls,
# and long runs with almost no sentence structure.
_NAV_COUNT_RE = re.compile(r"\(\s*[\d,]+\s*\)")
_NAV_WORD_RE = re.compile(r"\b(clear text|done|show more|apply filter|sort by|"
                          r"any time|past (month|week|24 hours)|see all)\b", re.I)


def _is_heading(text: str, pattern: re.Pattern) -> bool:
    """A heading, not a requirement that happens to contain a heading word.

    "Strong Python and Excel skills; experience with time series analysis
    preferred" matched the requirements-heading pattern on the word "skills"
    and was silently swallowed as a section header, dropping the single most
    relevant line in the posting. Real headings are short and terse.
    """
    if not pattern.search(text):
        return False
    if text.endswith(":"):
        return True
    return len(text) <= 70 and len(text.split()) <= 7


def _looks_like_nav(text: str) -> bool:
    if _NAV_WORD_RE.search(text):
        return True
    # Three or more "(1,234)" counts in one item is a filter list, not a duty.
    return len(_NAV_COUNT_RE.findall(text)) >= 3


def fetch(url: str, timeout: int = 25) -> str:
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse(html: str) -> dict:
    """Returns {title, company, description, requirements, responsibilities, text}."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    title = ""
    if soup.title:
        title = _clean(soup.title.get_text())
    h1 = soup.find("h1")
    if h1:
        title = _clean(h1.get_text()) or title

    company = ""
    meta_site = soup.find("meta", property="og:site_name")
    if meta_site and meta_site.get("content"):
        company = _clean(meta_site["content"])

    full_text = _clean(soup.get_text(" "))

    requirements, responsibilities = [], []
    bucket = None
    # Walk headings and lists in document order: a <ul> belongs to whichever
    # heading most recently preceded it.
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "strong", "b", "p", "li"]):
        text = _clean(el.get_text())
        if not text:
            continue
        is_header_tag = el.name in ("h1", "h2", "h3", "h4", "h5", "strong", "b")
        if is_header_tag or len(text) < 90:
            if _is_heading(text, _REQ_HEADING_RE) or (is_header_tag and _REQ_HEADING_RE.search(text)):
                bucket = requirements
                continue
            if _is_heading(text, _RESP_HEADING_RE) or (is_header_tag and _RESP_HEADING_RE.search(text)):
                bucket = responsibilities
                continue
        if el.name == "li" and bucket is not None:
            if 12 < len(text) < 400 and not _BOILERPLATE_RE.search(text):
                bucket.append(text)

    # Nothing structured found — fall back to every list item on the page, but
    # filter hard. LinkedIn's search chrome ("Past week (3,982) Done") is all
    # <li> too, and it sailed straight into the requirements list.
    if not requirements and not responsibilities:
        for li in soup.find_all("li"):
            text = _clean(li.get_text())
            if 25 < len(text) < 400 and not _BOILERPLATE_RE.search(text) \
                    and not _looks_like_nav(text):
                requirements.append(text)

    def dedupe(items):
        seen, out = set(), []
        for i in items:
            key = i.lower()
            if key not in seen:
                seen.add(key)
                out.append(i)
        return out

    return {
        "title": title,
        "company": company,
        "requirements": dedupe(requirements)[:25],
        "responsibilities": dedupe(responsibilities)[:20],
        "text": full_text,
        "thin": len(full_text) < 600,
    }


def parse_text(raw: str, title: str = "") -> dict:
    """Parses a posting you pasted in as plain text.

    This is the reliable path. Workday, LinkedIn and Salesforce render their
    postings in JavaScript or block non-browser requests outright, so fetching
    a URL returns an empty shell or a 403 — select-all on the page you already
    have open and paste it here instead.
    """
    lines = [_clean(l) for l in (raw or "").splitlines()]
    requirements, responsibilities = [], []
    bucket = None

    for line in lines:
        if not line:
            continue
        if _is_heading(line, _REQ_HEADING_RE):
            bucket = requirements
            continue
        if _is_heading(line, _RESP_HEADING_RE):
            bucket = responsibilities
            continue
        stripped = line.lstrip("-•*·–— \t")
        if bucket is not None and 12 < len(stripped) < 400 \
                and not _BOILERPLATE_RE.search(stripped):
            bucket.append(stripped)

    # No headings in the paste — treat every bullet-looking line as a duty.
    if not requirements and not responsibilities:
        for line in lines:
            if line.startswith(("-", "•", "*", "·", "–")) or 40 < len(line) < 300:
                stripped = line.lstrip("-•*·–— \t")
                if 25 < len(stripped) < 400 and not _BOILERPLATE_RE.search(stripped):
                    requirements.append(stripped)

    full_text = _clean(raw)
    return {
        "title": title or (lines[0] if lines else ""),
        "company": "",
        "requirements": requirements[:25],
        "responsibilities": responsibilities[:20],
        "text": full_text,
        "thin": len(full_text) < 300,
    }


def summarize(parsed: dict, limit: int = 480) -> str:
    """A short 'what this role actually is', for the Job Description column."""
    parts = []
    if parsed.get("responsibilities"):
        parts = parsed["responsibilities"][:3]
    elif parsed.get("requirements"):
        parts = parsed["requirements"][:3]

    if parts:
        summary = " ".join(parts)
    else:
        # No lists at all: take the densest prose near the top of the page.
        summary = parsed.get("text", "")[:1200]

    summary = _clean(summary)
    return textwrap.shorten(summary, width=limit, placeholder=" …")
