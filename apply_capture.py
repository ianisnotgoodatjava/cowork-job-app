"""Reads a job posting while it is still live, so the row is complete on day one.

Backfilling this later does not work: applications from last spring point at
postings that have been taken down, and LinkedIn serves an expired job as a
rail of *other* people's vacancies, which parses into plausible-looking
nonsense. The only reliable moment to capture a posting is the day you apply.

Order of preference:
  1. JSON-LD  — the schema.org JobPosting block. Greenhouse, Lever, Ashby,
     SmartRecruiters and most careers sites emit it, and it states title,
     employer, location and description as data rather than as layout.
  2. Meta tags / visible HTML — for sites that don't.
  3. Text you pasted — Workday and LinkedIn render in JavaScript or refuse
     non-browser requests, so nothing else works there.
"""

import html
import json
import re

import job_posting

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)
_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:(title|site_name)["\'][^>]+'
    r'content=["\']([^"\']+)["\']', re.I)
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# "Analyst, Fixed Income at Acme Capital | Acme Careers" -> title, company.
# Split on the last separator, and only on " at "/"|" unless nothing else
# matches: job titles are full of hyphens ("ABS Surveillance Data Entry
# Analyst (NY) - Kroll Bond Rating Agency" split on the first "-" loses half
# the title).
_TITLE_SPLIT_RE = re.compile(r"^(.*)\s+(?:at|@|\|)\s+(.+?)\s*$", re.I)
_TITLE_DASH_RE = re.compile(r"^(.*)\s+[-–—]\s+(.+?)\s*$")

# Fact-box rows ("Number of Tech Workers: 549,200") and page furniture that a
# generic bullet scrape mistakes for requirements.
_CHROME_RE = re.compile(
    r"^(?:[A-Z][\w /&-]{2,34}:\s)|"
    r"\b(number of (tech )?(workers|employees)|key industries|"
    r"major (tech )?employers|median salary|cost of living|"
    r"tech workforce|companies hiring|jobs in |view all|see all|"
    r"sign in|create (a |an )?account|cookie|newsletter)\b", re.I)


def _text(value) -> str:
    """schema.org fields arrive as a string, a dict, or a list of either."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "value", "@value"):
            if key in value:
                return _text(value[key])
        return ""
    if isinstance(value, list):
        for item in value:
            got = _text(item)
            if got:
                return got
    return ""


def _address(location) -> str:
    """'City, ST' out of a schema.org place."""
    if isinstance(location, list):
        parts = [_address(l) for l in location]
        parts = [p for p in parts if p]
        return " / ".join(dict.fromkeys(parts))
    if not isinstance(location, dict):
        return _text(location)
    addr = location.get("address")
    if isinstance(addr, str):
        return addr.strip()
    if not isinstance(addr, dict):
        return _text(location)
    city = _text(addr.get("addressLocality"))
    region = _text(addr.get("addressRegion"))
    if city and region:
        return f"{city}, {region}"
    return city or region or _text(addr.get("addressCountry"))


def _walk(node):
    """Yields every dict in a JSON-LD document, including @graph children."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def from_jsonld(page: str) -> dict | None:
    for block in _JSONLD_RE.findall(page or ""):
        try:
            data = json.loads(html.unescape(block.strip()))
        except ValueError:
            continue
        for node in _walk(data):
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if not any(str(t).lower() == "jobposting" for t in types):
                continue
            description = _text(node.get("description"))
            return {
                "title": _text(node.get("title")),
                "company": _text(node.get("hiringOrganization")),
                "location": _address(node.get("jobLocation"))
                            or ("Remote" if node.get("jobLocationType") else ""),
                "description_html": description,
            }
    return None


# og:site_name on an aggregator names the board, not the employer. "Built In
# NYC" is not who you applied to.
_JOB_BOARD_RE = re.compile(
    r"^(built ?in|linkedin|indeed|ziprecruiter|glassdoor|monster|dice|"
    r"wellfound|angellist|simplyhired|efinancialcareers|greenhouse|lever|"
    r"workday|smartrecruiters|jobvite|icims|taleo|ashby|workable|"
    r"handshake|hiring ?cafe|otta|welcome to the jungle)", re.I)


def _from_meta(page: str) -> dict:
    found = {k.lower(): html.unescape(v) for k, v in _META_RE.findall(page or "")}
    title = found.get("title", "")
    company = found.get("site_name", "")
    if company and _JOB_BOARD_RE.match(company.strip()):
        company = ""
    if not title:
        m = _TITLE_TAG_RE.search(page or "")
        title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    if title and not company:
        m = _TITLE_SPLIT_RE.match(title) or _TITLE_DASH_RE.match(title)
        if m:
            title, company = m.group(1).strip(), m.group(2).strip()
    return {"title": title, "company": company, "location": "",
            "description_html": ""}


def capture(url: str = "", pasted: str = "") -> dict:
    """Everything the sheet needs about a posting.

    `thin` is set when the page came back without enough text to trust — a
    JavaScript-only posting or a 403. The caller should say so rather than
    write a confident-looking empty row.
    """
    result = {"title": "", "company": "", "location": "", "requirements": [],
              "link": url, "thin": True, "source": ""}

    if pasted:
        parsed = job_posting.parse_text(pasted)
        result.update(title=parsed.get("title", ""),
                      requirements=parsed.get("requirements", []),
                      thin=parsed.get("thin", True), source="pasted text")
        return result

    page = job_posting.fetch(url)
    structured = from_jsonld(page)
    result["source"] = "JSON-LD" if structured else "page HTML"
    if not structured:
        structured = _from_meta(page)

    parsed = job_posting.parse(page)
    # The JSON-LD description is itself HTML; parsing it beats parsing the whole
    # page, which drags in nav, footers and "similar jobs".
    detail = (job_posting.parse(structured["description_html"])
              if structured.get("description_html") else parsed)

    requirements = [r for r in (detail.get("requirements")
                                or parsed.get("requirements") or [])
                    if not _CHROME_RE.search(r)]
    # The guard has to sit here, not only in _from_meta: job_posting.parse
    # reads the page's own branding too, so the board's name comes back by
    # that route as well.
    company = structured.get("company") or parsed.get("company", "")
    if company and _JOB_BOARD_RE.match(company.strip()):
        company = ""

    result.update(
        title=structured.get("title") or parsed.get("title", ""),
        company=company,
        location=structured.get("location", ""),
        requirements=requirements,
        # Judged on the payload, not on page length: a JavaScript-rendered
        # posting still ships kilobytes of navigation, so "the page had text"
        # says nothing about whether the job was on it.
        thin=not requirements,
    )
    return result


def requirements_cell(requirements: list[str], limit: int = 900) -> str:
    """The requirement bullets as one cell, shortest-first so the specific
    ones survive the length cap rather than one long paragraph eating it."""
    cleaned = []
    for r in requirements:
        r = re.sub(r"\s+", " ", r).strip(" •-–—*\t")
        if 15 <= len(r) <= 240:
            cleaned.append(r)
    out = []
    for r in sorted(cleaned, key=len)[:8]:
        if sum(len(x) + 2 for x in out) + len(r) > limit:
            break
        out.append(r)
    # Restore the posting's own order for the ones that made the cut.
    return "; ".join([r for r in cleaned if r in out])
