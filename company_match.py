"""Shared company-name matching for the Gmail scanners.

Uses word-boundary regex, not plain substring containment — a company like
"ING" normalizes to "ing", which is a substring of ordinary words like
"applying" or "interesting". Plain `"ing" in text` produced false-positive
matches on nearly every email. Word boundaries plus a minimum length guard
against that.
"""

import re

_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|group|holdings|partners|lp|llp)\.?\b",
    re.IGNORECASE,
)


def normalize(name: str) -> str:
    name = _SUFFIX_RE.sub("", name)
    name = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def company_mentioned(company: str, haystack: str) -> bool:
    normalized = normalize(company)
    # Word boundaries (below) already stop "ING" matching inside "applying",
    # so a length floor is unnecessary and blocked real short names like CME,
    # UBS, BNY and NRG from ever matching.
    if len(normalized) < 2:
        return False
    pattern = r"\b" + re.escape(normalized).replace(r"\ ", r"\s+") + r"\b"
    return re.search(pattern, haystack) is not None
