"""Picks how to find a real person at a company, per company.

There is no single source that covers everything applied to here. The list
runs from global broker-dealers to rating agencies to eight-person clean
energy developers, and each kind exposes its people differently:

  BROKERCHECK   Registered broker-dealers must disclose every registered
                person to FINRA, with branch city and the date they entered
                the industry. Structured, live, and free. Best available
                source when it applies, which is most banks, brokerages and
                trading firms.
  PUBLISHED     Rating agencies and research houses print an analytical
                contact block -- name, title, phone, email -- on every
                rating action, and the piece doubles as the hook. Nothing
                needs inferring.
  TEAM_PAGE     Small private firms often list everyone on one page. Cheap
                to try, unreliable to depend on: many render in JavaScript
                and return nothing useful to a plain fetch.
  SEARCH        Everyone else. Still the fallback, but a targeted query
                beats "who works at X" -- the role's own vocabulary is what
                surfaces a practitioner instead of a press release.

The point is that the choice is made per company rather than defaulting to
one method for all of them. Ranking is by how verifiable the result is:
a FINRA registration is a fact, a published contact block is a fact, and a
search result is a lead.

Read-only. Suggests people; never writes a draft or touches the sheet.
"""

import re

import people_finder
from findability import _PUBLISHERS

BROKERCHECK = "BROKERCHECK"
PUBLISHED = "PUBLISHED"
TEAM_PAGE = "TEAM_PAGE"
SEARCH = "SEARCH"

# Tried in this order on a company's own domain.
_TEAM_PATHS = ("/team", "/our-team", "/about/team", "/people", "/about-us",
               "/about", "/leadership")

# Role vocabulary worth carrying into a search query. A bare company name
# returns the newsroom; the desk's own words return practitioners.
_ROLE_HINT_RE = re.compile(
    r"\b(abs|cmbs|rmbs|clo|securitiz\w+|structured|fixed income|credit|"
    r"rates|fx|foreign exchange|derivativ\w+|commodit\w+|energy|power|"
    r"equity|research|portfolio|trading|trader|underwrit\w+|actuar\w+|"
    r"reinsurance|insurance|project finance|infrastructure|municipal|"
    r"private credit|fund financ\w+|treasury|risk|quantitative|macro)\b",
    re.I,
)


def _is_broker_dealer(company: str) -> tuple[str, str] | None:
    """(firm_id, firm_name) if FINRA knows this as a firm, else None."""
    try:
        firms = people_finder.find_firm(company, limit=5)
    except Exception:
        return None
    return firms[0] if firms else None


def _publishes(company: str) -> bool:
    lower = (company or "").lower()
    return any(name in lower for name in _PUBLISHERS)


def search_query(company: str, role: str = "") -> str:
    """The query to actually run, not a generic one.

    Pins to LinkedIn profiles, carries the desk's vocabulary, and names the
    seniorities that reply — without those it returns the company's press
    office and its chief executive.
    """
    hints = " OR ".join(sorted({h.lower() for h in _ROLE_HINT_RE.findall(role or "")}))
    focus = f'"{hints}" ' if hints else ""
    return (f'"{company}" {focus}analyst OR associate OR "vice president" '
            f'site:linkedin.com/in')


def team_page_urls(company: str) -> list[str]:
    """Plausible staff-page URLs for a company with no obvious domain."""
    slug = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    if not slug:
        return []
    return [f"https://www.{slug}.com{path}" for path in _TEAM_PATHS[:4]]


def plan(company: str, role: str = "") -> list[dict]:
    """Ordered strategies for this company, most verifiable first."""
    steps: list[dict] = []

    if _publishes(company):
        steps.append({
            "method": PUBLISHED,
            "why": "rating agency or research house — analytical contacts "
                   "are printed on every rating action, with the email",
            "how": f'search: "{company}" "Analytical Contacts" {role[:40]}',
        })

    firm = _is_broker_dealer(company)
    if firm:
        steps.append({
            "method": BROKERCHECK,
            "why": f"registered broker-dealer (FINRA firm {firm[0]}: "
                   f"{firm[1]})",
            "how": f'./venv/bin/python cli.py people --company "{company}"',
        })

    if not firm and not _publishes(company):
        steps.append({
            "method": TEAM_PAGE,
            "why": "no FINRA registration and not a publisher — small or "
                   "private firms often list staff on one page",
            "how": ", ".join(team_page_urls(company)[:3]) or "(no domain guess)",
        })

    steps.append({
        "method": SEARCH,
        "why": "always available; the query matters more than the engine",
        "how": search_query(company, role),
    })
    return steps


def run(company: str, role: str = "", state: str = "NY") -> None:
    print(f"{company}" + (f"  —  {role}" if role else "") + "\n")
    for i, step in enumerate(plan(company, role), 1):
        print(f"{i}. {step['method']}")
        print(f"   why: {step['why']}")
        print(f"   how: {step['how']}\n")

    firm = _is_broker_dealer(company)
    if firm:
        print("-" * 66)
        people_finder.run(company, state=state, limit=150, show=10)
