"""Finds real, currently-registered people at a firm, from FINRA BrokerCheck.

LinkedIn is the slow path: it rate-limits, it cannot be queried
programmatically without violating its terms, and what it returns is
whatever its relevance ranking feels like surfacing — often a Managing
Director when the target is an analyst, and often someone who left two
years ago.

BrokerCheck is the opposite. It is a public regulatory disclosure system
whose entire purpose is letting anyone look up who is registered where, it
answers in a few hundred milliseconds, and a person only appears against a
firm while their registration there is actually current. Querying it is the
intended use, not a workaround.

What it gives, per person:
  * legal name
  * the firm they are registered with right now
  * branch city and state — so the search can be held to New York
  * the date they entered the industry, which stands in for seniority

What it does not give is the desk or job title. So this is the first half of
sourcing, not all of it: it produces a verified roster of real people at the
firm, and the name is then specific enough to search against for function.
That is a much better starting point than asking a search engine "who works
in fund financing at Nomura" and hoping.

Coverage is broker-dealers, which is most of what this search targets —
banks, brokerages and trading firms. Registered investment advisers do not
list individuals this way (the adviserinfo endpoint returns none), so pure
asset managers still need another route.

Read-only, unauthenticated, and nothing here writes to the sheet or Gmail.
"""

import json
import re
import time
import urllib.parse
import urllib.request

_FIRM_URL = "https://api.brokercheck.finra.org/search/firm"
_IND_URL = "https://api.brokercheck.finra.org/search/individual"
# BrokerCheck rejects the default urllib agent.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Someone who entered the industry within this window is plausibly an
# analyst, associate, VP or director rather than a desk head — the band that
# actually replies. Not a title, but the only seniority signal the registry
# carries.
MID_LEVEL_YEARS = (2, 18)


def _get(url: str, params: dict, timeout: int = 25) -> dict:
    query = urllib.parse.urlencode({**params, "hl": "true", "wt": "json"})
    req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


# Words shared by half the registry, which prove nothing about identity.
_GENERIC_FIRM_WORDS = {
    "llc", "inc", "corp", "corporation", "company", "co", "the", "group",
    "capital", "securities", "partners", "lp", "llp", "holdings", "markets",
    "financial", "investments", "investment", "management", "advisors",
    "advisory", "america", "americas", "usa", "us", "international",
    "global", "and", "of", "limited", "ltd",
}


def _distinctive(name: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return {w for w in words if w not in _GENERIC_FIRM_WORDS and len(w) > 2}


def find_firm(name: str, limit: int = 8) -> list[tuple[str, str]]:
    """(firm_id, firm_name) for firms whose name really matches this one.

    FINRA's search is fuzzy and will happily answer a query it only matched
    on boilerplate: "Madison-Davis, LLC" came back as "THE ROBINSON-HUMPHREY
    COMPANY, LLC", which shares nothing but the legal suffix. A result has
    to share a distinctive word — one that is not "capital", "securities",
    "LLC" and the rest of the vocabulary half the registry uses.
    """
    data = _get(_FIRM_URL, {"query": name, "nrows": limit, "start": 0})
    wanted = _distinctive(name)
    out = []
    for hit in (data.get("hits", {}).get("hits") or []):
        src = hit.get("_source") or {}
        firm_name = src.get("firm_name")
        if not (src.get("firm_source_id") and firm_name):
            continue
        # Primary name only. firm_other_names is a graveyard of acquired and
        # retired DBAs — Robinson-Humphrey carries two dozen, among them
        # "FIRST MADISON ADVISORS" and "DAVIS SKAGGS INVESTMENT MANAGEMENT",
        # which is how a staffing agency called Madison-Davis matched a
        # defunct Atlanta brokerage.
        # Compared with spaces removed as well, because firms are written
        # both ways: the sheet says "VanEck", the registry says "VAN ECK
        # ASSOCIATES CORP.", and word-set overlap alone sees nothing in
        # common. Squashing is still specific enough that "madisondavis"
        # does not appear anywhere in "therobinsonhumphreycompany".
        squashed_q = re.sub(r"[^a-z0-9]", "", (name or "").lower())
        squashed_f = re.sub(r"[^a-z0-9]", "", firm_name.lower())
        related = bool(_distinctive(firm_name) & wanted) or (
            len(squashed_q) >= 5 and squashed_q in squashed_f)
        if wanted and not related:
            continue
        out.append((str(src["firm_source_id"]), firm_name))
    return out


def people_at_firm(firm_id: str, state: str = "", limit: int = 200) -> list[dict]:
    """Everyone currently registered with this firm, newest entrants first."""
    people, start, page = [], 0, 50
    while start < limit:
        data = _get(_IND_URL, {"firm": firm_id, "nrows": page, "start": start})
        hits = data.get("hits", {}).get("hits") or []
        if not hits:
            break
        for hit in hits:
            src = hit.get("_source") or {}
            emp = (src.get("ind_current_employments") or [{}])[0]
            if state and (emp.get("branch_state") or "").upper() != state.upper():
                continue
            started = (src.get("ind_industry_cal_date") or "")[:10]
            people.append({
                "name": f"{src.get('ind_firstname', '')} "
                        f"{src.get('ind_lastname', '')}".strip(),
                "firm": emp.get("firm_name", ""),
                "city": emp.get("branch_city", ""),
                "state": emp.get("branch_state", ""),
                "started": started,
                "years": _years_since(started),
            })
        start += page
        time.sleep(0.3)  # this is a free public service; do not hammer it

    # One person can hold several registrations at the same firm and comes
    # back once per registration.
    seen, unique = set(), []
    for person in people:
        key = (person["name"].lower(), person["city"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(person)
    unique.sort(key=lambda p: p["started"], reverse=True)
    return unique


def _years_since(date_str: str) -> int | None:
    import datetime
    try:
        started = datetime.date.fromisoformat(date_str)
    except ValueError:
        return None
    return (datetime.date.today() - started).days // 365


def mid_level(people: list[dict]) -> list[dict]:
    low, high = MID_LEVEL_YEARS
    return [p for p in people
            if p["years"] is not None and low <= p["years"] <= high]


def run(company: str, state: str = "NY", limit: int = 200,
        show: int = 20) -> None:
    firms = find_firm(company)
    if not firms:
        print(f"No registered broker-dealer matches {company!r}.")
        print("BrokerCheck only covers broker-dealers — a pure asset manager "
              "or a private firm will not appear here.")
        return

    print(f"Firms matching {company!r}:")
    for fid, name in firms[:5]:
        print(f"  {fid:>8}  {name}")

    # A group registers several entities and the closest name match is often
    # the wrong one: "Nomura Securities North America" ranks above "Nomura
    # Securities International", but the staff are all registered against
    # the latter. Walk the candidates until one actually has people.
    people, firm_name = [], ""
    for firm_id, name in firms[:5]:
        print(f"\nChecking {name} ({firm_id})"
              f"{f', {state} only' if state else ''} ...", flush=True)
        found = people_at_firm(firm_id, state=state, limit=limit)
        if found:
            people, firm_name = found, name
            break
        print("  none registered there")
    if not people:
        print("\nNobody registered at any matching entity in that state.")
        return
    print(f"\n{firm_name}\n")

    band = mid_level(people)
    print(f"{len(people)} registered {state or ''} people; "
          f"{len(band)} in the {MID_LEVEL_YEARS[0]}-{MID_LEVEL_YEARS[1]} "
          f"year band\n")
    for person in band[:show]:
        print(f"  {person['started']}  {person['years']:>2}y  "
              f"{person['name'][:32]:32s} {person['city']}, {person['state']}")
    if len(band) > show:
        print(f"  ... and {len(band) - show} more")
    print("\nThese are real people currently registered there. BrokerCheck "
          "does not\ncarry the desk, so search a name to find function "
          "before writing.")
