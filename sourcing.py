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
  EDGAR         Everyone else with a fund: the SEC requires prospectuses to
                name whoever runs the money. Free, full-text searchable, and
                the only structured route into pure asset managers, which
                FINRA does not register.
  FORM_D        Private funds raising under Reg D must file a Form D, whose
                Related Persons list names officers and directors in XML.
                The only structured route into firms too small or too
                private to publish a prospectus.
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
from functools import lru_cache

import people_finder
from findability import _PUBLISHERS

BROKERCHECK = "BROKERCHECK"
EDGAR = "EDGAR"
FORM_D = "FORM_D"
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


@lru_cache(maxsize=256)
def _is_broker_dealer(company: str) -> tuple[str, str] | None:
    """(firm_id, firm_name) if FINRA knows this as a firm, else None.

    Cached because plan() and run() both need the answer, and a sweep over
    the sheet asks about the same company repeatedly — without this every
    caller paid a fresh network round trip for a fact that cannot change
    inside one run.
    """
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
        # BrokerCheck only registers people at broker-dealers, so a pure
        # asset manager returns nobody — and those are exactly the firms
        # posting "Portfolio Analyst". Fund filings name whoever runs the
        # money, and are free and full-text searchable.
        steps.append({
            "method": EDGAR,
            "why": "not a broker-dealer — SEC fund filings name portfolio "
                   "managers, which FINRA does not cover",
            "how": f'./venv/bin/python cli.py edgar --company "{company}"',
        })
        steps.append({
            "method": FORM_D,
            "why": "private funds name officers and directors in Form D, the "
                   "only structured source for firms with no prospectus",
            "how": f'./venv/bin/python cli.py edgar --company "{company}" --form-d',
        })
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
    elif not _publishes(company):
        import edgar_finder
        print("-" * 66)
        try:
            edgar_finder.run(company)
        except Exception as exc:
            print(f"EDGAR lookup failed: {type(exc).__name__}: {str(exc)[:80]}")


# ---------------------------------------------------------------------------
# The automated half.
#
# Everything above was reachable only by typing a command, which meant it
# contributed nothing to the scheduled run -- the same mistake draft_cleanup
# made by gating on a column that no longer existed. The coverage step knew
# which applications had no draft and stopped at saying so; this turns that
# list into names.
#
# Deliberately only the cheap sources run unattended. BrokerCheck answers in
# milliseconds off one request, so a roster is affordable for every
# broker-dealer in the gap. EDGAR has to download and parse whole filings,
# which is minutes, so for those the plan is printed and the fetch is left
# to be run by hand.

def candidates(company: str, role: str = "", state: str = "NY",
               top: int = 3) -> tuple[str, list[dict]]:
    """(method, people) for one company, using only the fast sources."""
    steps = plan(company, role)
    method = steps[0]["method"]
    if method != BROKERCHECK:
        return method, []
    firm = _is_broker_dealer(company)
    if not firm:
        return method, []
    try:
        people = people_finder.people_at_firm(firm[0], state=state, limit=100)
    except Exception:
        return method, []
    band = people_finder.mid_level(people)
    return method, (band or people)[:top]


def worklist(rows: list[dict], limit: int = 12, state: str = "NY") -> None:
    """Prints who to write to, for applications that still have no draft."""
    if not rows:
        return
    print(f"\nWho to contact, for the {min(len(rows), limit)} most recent "
          f"of these:\n", flush=True)

    seen: set[str] = set()
    shown = 0
    for row in rows:
        if shown >= limit:
            break
        company = (row.get("Company") or "").strip()
        key = company.lower()
        if not company or key in seen:
            continue
        seen.add(key)
        shown += 1

        try:
            method, people = candidates(company, row.get("Position", ""), state)
        except Exception as exc:
            print(f"  {company[:30]:30s} lookup failed: {type(exc).__name__}")
            continue

        print(f"  {company[:30]:30s} [{method}]")
        for person in people:
            print(f"      {person['name'][:30]:30s} "
                  f"{person['years']}y in industry, {person['city']}")
        if not people:
            step = next((s for s in plan(company, row.get("Position", ""))
                         if s["method"] == method), None)
            if step:
                print(f"      {step['how'][:88]}")
