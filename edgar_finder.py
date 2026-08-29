"""Finds named investment professionals in SEC filings.

This covers the hole BrokerCheck leaves. FINRA only registers people at
broker-dealers, so a pure asset manager — PineBridge, Russell, VanEck's
advisory arm — returns nobody, and those are exactly the firms whose job
postings say "Portfolio Analyst".

The SEC fills it from the other side. A fund prospectus has to name the
people who actually run the money, with their titles and usually their
tenure, and every one of those filings is public, free, and full-text
searchable back to 2001. Nobody has to be scraped.

Two things make the result trustworthy rather than a pile of names:

  * Full-text search matches any filing that *mentions* the firm, which is
    not the same as a filing *about* it. Searching PineBridge returns a
    Dunham Funds prospectus, because PineBridge is a subadviser named
    inside it. So a name only counts when it appears near the firm's own
    name in the text.
  * Titles beside a name are noise as often as signal ("Managing Director"
    reads like a person to a regex). Anything that is a job title rather
    than a name is dropped.

Read-only, unauthenticated. The SEC asks for a contact address in the
User-Agent and rate-limits at 10 requests/second; both are respected.
"""

import html
import json
import re
import time
import urllib.parse
import urllib.request

_FTS = "https://efts.sec.gov/LATEST/search-index"
_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
# The SEC requires a real contact address in the User-Agent.
_UA = "Ian Liu icl2016@nyu.edu"

# Forms that name the people running a strategy.
DEFAULT_FORMS = "485BPOS,N-CSR,N-CSRS,DEF 14A"

_NAME = r"[A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z]+)?(?:-[A-Z][a-z]+)?\s+[A-Z][a-z]+"
_ROLE = (r"Portfolio Manager|Senior Portfolio Manager|Co-Portfolio Manager|"
         r"Lead Portfolio Manager|Portfolio Analyst|Research Analyst|"
         r"Senior Analyst|Credit Analyst")
_PERSON_RE = re.compile(rf"({_NAME}),?\s+(?:is\s+)?(?:a\s+|the\s+)?(?:{_ROLE})")

# Strings the name pattern happily matches that are not people.
_NOT_A_NAME = re.compile(
    r"\b(managing director|senior director|vice president|chief|officer|"
    r"the fund|the trust|the adviser|the company|portfolio manager|"
    r"investment management|asset management|capital management|senior|"
    r"united states|new york|limited|holdings)\b", re.I)

# Ordinary words that capitalise mid-sentence in a prospectus and so pair up
# into convincing-looking names: "Account Type", "Interest When".
_NOT_NAME_WORDS = {
    "account", "type", "interest", "when", "class", "fund", "funds", "share",
    "shares", "trust", "series", "total", "annual", "return", "returns",
    "expense", "expenses", "average", "index", "value", "growth", "income",
    "capital", "manager", "management", "investment", "investments", "risk",
    "market", "markets", "credit", "equity", "bond", "bonds", "global",
    "master", "adviser", "advisor", "board", "table", "period", "year",
    "years", "since", "inception", "portfolio", "strategy", "sub", "the",
}


def _get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def filings(company: str, forms: str = DEFAULT_FORMS,
            since: str = "2024-01-01", limit: int = 5) -> list[dict]:
    """Recent filings whose text mentions this company."""
    # Both phrases, or the search returns whatever exhibit happens to name
    # the firm — a code-of-ethics attachment ranked above every prospectus
    # and contained no people at all.
    query = urllib.parse.urlencode({
        "q": f'"{company}" "Portfolio Manager"', "forms": forms,
        "dateRange": "custom", "startdt": since,
        "enddt": time.strftime("%Y-%m-%d"),
    })
    data = json.loads(_get(f"{_FTS}?{query}"))
    out = []
    for hit in (data.get("hits", {}).get("hits") or [])[:limit]:
        src = hit.get("_source") or {}
        ciks = src.get("ciks") or []
        if ":" not in hit.get("_id", "") or not ciks:
            continue
        accession, filename = hit["_id"].split(":", 1)
        out.append({
            "filer": (src.get("display_names") or [""])[0],
            "url": f"{_ARCHIVE}/{ciks[0].lstrip('0')}/"
                   f"{accession.replace('-', '')}/{filename}",
        })
    return out


def people_in(text: str, company: str, window: int = 3000) -> list[str]:
    """Names given an investment title *near a mention of the company*.

    The proximity test is the whole point. Without it, searching one firm
    returns the portfolio managers of every subadviser named in the same
    prospectus.
    """
    flat = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    core = re.escape(company.split()[0])
    zones = [(m.start(), m.end()) for m in re.finditer(core, flat, re.I)]
    if not zones:
        return []

    found: list[str] = []
    for match in _PERSON_RE.finditer(flat):
        name = match.group(1).strip(" ,")
        words = name.split()
        if _NOT_A_NAME.search(name) or len(words) < 2:
            continue
        if any(w.lower().strip(".") in _NOT_NAME_WORDS for w in words):
            continue
        pos = match.start()
        if not any(start - window <= pos <= end + window for start, end in zones):
            continue
        if name not in found:
            found.append(name)
    return found


def find(company: str, since: str = "2024-01-01", limit: int = 3) -> list[dict]:
    """[{name, filer, url}] for people named in this company's filings."""
    results: list[dict] = []
    seen: set[str] = set()
    for filing in filings(company, since=since, limit=limit):
        try:
            text = _get(filing["url"])
        except Exception:
            continue
        for name in people_in(text, company):
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            results.append({"name": name, "filer": filing["filer"],
                            "url": filing["url"]})
        time.sleep(0.2)  # SEC asks for under 10 requests a second
    return results


def run(company: str, since: str = "2024-01-01") -> None:
    print(f"SEC filings naming people at {company!r} (since {since})\n",
          flush=True)
    people = find(company, since=since)
    if not people:
        print("No named investment professionals found.")
        print("Filings may not name individuals, or the firm may not appear "
              "in fund filings at all.")
        return
    for person in people:
        print(f"  {person['name'][:34]:34s} via {person['filer'][:40]}")
    print(f"\n{len(people)} name(s). These come from filings that mention "
          f"{company};\nconfirm the person is actually there before writing.")
