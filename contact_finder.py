"""Finds outreach-contact candidates via regular web search (not LinkedIn
automation — this queries a public search index, never visits/scrapes
linkedin.com directly).

Standing targeting policy, learned from feedback during setup:
  - Skip HR / recruiting / talent acquisition (they won't give real insight
    into the role, and outreach usually isn't addressed to them anyway).
  - Skip top leadership (Global Head, Chief *, Managing Director+) — too
    senior to realistically respond to a student/early-career networking
    email.
  - Target "mid-level" for the industry, not a fixed title string. A title
    like "VP" means something different per industry: in finance/banking
    (Analyst -> Associate -> VP -> Director -> MD -> C-suite), VP is
    mid-level and a good target. At a tech company, "VP" usually *is*
    senior leadership, where "Manager" or "Senior/Lead" is the mid-level
    equivalent. So both finance-style and tech-style mid-level titles are
    included, and filtering happens by role band rather than one title.
"""

import re
import gmail_client  # noqa: F401  (kept for symmetry with other scanners; not used directly)
from company_match import normalize

# Finance-style mid-level ladder + tech-style mid-level equivalents.
SENIORITY_INCLUDE = (
    '(Director OR "Vice President" OR VP OR Associate OR Manager OR '
    '"Senior Manager" OR "Team Lead" OR "Senior Analyst")'
)
EXCLUDE_TERMS = '-recruiter -"talent acquisition" -"human resources" -HR -"global head" -"chief " -CEO -CFO -COO'

_TOO_SENIOR_PATTERNS = (
    "global head", "chief ", " ceo", " cfo", " coo", "managing director",
    "president,", "founder", "chairman", "svp", "senior vice president",
    "evp", "executive vice president",
)
_HR_PATTERNS = ("talent acquisition", "recruiter", "human resources", " hr ", "recruiting")


def build_query(company: str, team_hint: str = "") -> str:
    """team_hint should describe the industry/team context (e.g. 'RMBS' for
    a finance role, 'engineering' for tech) — the mid-level title set is
    broad on purpose, and company/context narrows it down."""
    parts = [f"site:linkedin.com/in {company}"]
    if team_hint:
        parts.append(f'"{team_hint}"')
    parts.append(SENIORITY_INCLUDE)
    parts.append(EXCLUDE_TERMS)
    return " ".join(parts)


def filter_candidates(results: list[dict], company: str) -> list[dict]:
    """results: [{"title": ..., "url": ...}]. Filters out wrong-company
    matches (e.g. 'KBR' vs 'KBRA') and over/under-seniority matches."""
    company_norm = normalize(company)
    kept = []
    for r in results:
        title_lower = r["title"].lower()
        if company_norm and company_norm not in re.sub(r"[^a-z0-9 ]", "", title_lower):
            continue
        if any(p in title_lower for p in _TOO_SENIOR_PATTERNS):
            continue
        if any(p in title_lower for p in _HR_PATTERNS):
            continue
        kept.append(r)
    return kept
