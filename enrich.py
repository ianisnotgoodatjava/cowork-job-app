"""Fills in what the posting says, for every row that has a link to one.

The point is the reach-out. Writing to somebody about a role goes better when
the sheet already holds what the posting asked for and when it went up, rather
than requiring the posting to be re-opened and re-read every time.

Fills four columns from the posting itself:
  * Required Skills & Experience — a short summary of the qualifications
  * Posted   — the date the posting went live
  * location — where the role actually is
  * Job ID   — the requisition number

The primary source is the JSON-LD `JobPosting` block that Workday, Greenhouse,
LinkedIn and most other ATS pages embed. It is structured, so it gives the
posting date and location exactly rather than by guessing at prose. Pages
without it fall back to `job_posting.parse`, which reads the HTML.

Nothing is invented. A page that 404s, sits behind a login, or renders only
through JavaScript yields nothing, and the row is left as it was. Postings
expire, so on a sheet this old a low hit rate is the honest outcome.
"""

import datetime
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import job_posting
import sheets

# Rows whose link has already been fetched, successfully or not. Without it
# every scheduled pass would re-fetch hundreds of dead postings.
STATE_FILE = os.path.join("logs", "enriched.json")
WORKERS = 8


def _state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)


def _walk(node):
    """Yields every dict in a JSON-LD document, which may nest or be a list."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def job_posting_ld(html: str) -> dict:
    """The JSON-LD JobPosting block, or {}."""
    for blob in _LD_RE.findall(html or ""):
        try:
            data = json.loads(blob.strip())
        except ValueError:
            continue
        for node in _walk(data):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" in [t for t in types if t]:
                return node
    return {}


def _location_from_ld(ld: dict) -> str:
    places = ld.get("jobLocation")
    places = places if isinstance(places, list) else [places]
    for place in places:
        if not isinstance(place, dict):
            continue
        addr = place.get("address")
        if not isinstance(addr, dict):
            continue
        city = addr.get("addressLocality") or ""
        region = addr.get("addressRegion") or ""
        country = addr.get("addressCountry") or ""
        if isinstance(country, dict):
            country = country.get("name", "")
        parts = [p for p in (city, region) if p]
        if parts:
            return ", ".join(parts)
        if country:
            return str(country)
    return ""


def _posted_from_ld(ld: dict) -> str:
    raw = ld.get("datePosted") or ""
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        return datetime.date.fromisoformat(raw[:10]).strftime("%-m/%-d/%Y")
    except ValueError:
        return raw[:10]


def _req_from_ld(ld: dict) -> str:
    ident = ld.get("identifier")
    if isinstance(ident, dict):
        ident = ident.get("value") or ident.get("name") or ""
    if isinstance(ident, (str, int)):
        text = str(ident).strip()
        # Some pages put the company name here instead of a requisition.
        if text and re.search(r"\d", text) and len(text) <= 32:
            return text
    return ""


# A careers landing page is not a posting. Reading one gives you the company's
# benefits page and boilerplate, which is worse than an empty cell because it
# looks like real data. A posting URL carries an id or a detail path.
_SPECIFIC_URL_RE = re.compile(
    r"/details/|/job/|/jobs/view/|/position/|"
    r"(?:/|[?&=_-])(?:R-?)?\d{4,}\b", re.I)

# Benefits copy trips the requirements heading matcher ("What we offer"), and
# the result reads as if the role required a health savings account.
_BENEFITS_RE = re.compile(
    r"\b(medical coverage|dental|vision|401\(?k\)?|health savings|"
    r"spending account|paid time off|pto\b|parental leave|tuition "
    r"reimbursement|employer contributions|well-?being|life insurance|"
    r"eligible dependents|domestic partners)\b", re.I)


def _is_benefits(text: str) -> bool:
    """True when a 'requirements' summary is really the benefits section."""
    return len(_BENEFITS_RE.findall(text or "")) >= 2


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def enrich_one(url: str) -> dict:
    """Everything worth storing about one posting, or {} if unreachable."""
    try:
        html = job_posting.fetch(url)
    except Exception:
        return {}
    if not html:
        return {}

    ld = job_posting_ld(html)
    out = {
        "location": _location_from_ld(ld),
        "Posted": _posted_from_ld(ld),
        "Job ID": _req_from_ld(ld),
    }

    # Qualifications: the parsed requirement bullets read better than the raw
    # description, so try them first.
    summary = ""
    try:
        parsed = job_posting.parse(html)
        if parsed.get("requirements"):
            summary = job_posting.summarize(parsed)
    except Exception:
        parsed = {}
    if not summary:
        desc = _strip_tags(ld.get("description", ""))
        # Run the same requirements parser over the JSON-LD description
        # instead of taking its first paragraph: that paragraph is the
        # company's marketing blurb ("Diversified. Resilient. Dynamic."),
        # which says nothing about what the role asks for.
        if len(desc) > 120:
            try:
                from_desc = job_posting.parse_text(desc)
                if from_desc.get("requirements"):
                    summary = job_posting.summarize(from_desc)
            except Exception:
                pass
        if not summary and len(desc) > 120:
            summary = desc[:480].rsplit(" ", 1)[0] + " …"
    if _is_benefits(summary):
        summary = ""
    out["Required Skills & Experience"] = summary
    return {k: v for k, v in out.items() if v}


def run(limit: int = 200, dry_run: bool = True, retry_failed: bool = False,
        only_missing: bool = True) -> None:
    rows = sheets.list_rows()
    state = _state()

    targets = []
    for row in rows:
        url = (row.get("Job Link") or "").strip()
        if not url.startswith("http"):
            continue
        if not _SPECIFIC_URL_RE.search(url):
            continue
        key = str(row["row_number"]) + "|" + url
        if key in state and not (retry_failed and state[key] == "empty"):
            continue
        if only_missing and all(
                (row.get(c) or "").strip()
                for c in ("Required Skills & Experience", "Posted", "location")):
            continue
        targets.append((row, url, key))
    targets = targets[:limit]

    print(f"{len(targets)} row(s) with a link left to read "
          f"(of {len(rows)} rows)\n", flush=True)
    if not targets:
        return

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda t: enrich_one(t[1]), targets))

    patches, filled = {}, 0
    for (row, url, key), data in zip(targets, results):
        if not data:
            state[key] = "empty"
            continue
        # Never overwrite something already in the sheet: a hand-typed
        # location is better than one scraped off an aggregator.
        patch = {c: v for c, v in data.items()
                 if not (row.get(c) or "").strip()}
        state[key] = "ok"
        if not patch:
            continue
        patches[row["row_number"]] = patch
        filled += 1
        cols = ", ".join(sorted(patch))
        print(f"  row {row['row_number']:3d} | {row['Company'][:22]:22s} | {cols}")

    print(f"\n{filled} row(s) enriched; "
          f"{len(targets) - filled} yielded nothing (expired, login-walled, "
          f"or JavaScript-only).")
    if dry_run:
        print("\n(dry-run — nothing written)")
        return
    written = sheets.update_rows(patches)
    _save_state(state)
    print(f"\nWrote {written} cell(s) across {len(patches)} row(s).")
