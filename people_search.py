"""Finds a named person on the right desk, ranked, addressed and de-duplicated.

Sourcing was three unconnected things: people_finder queried FINRA, edgar_finder
queried SEC filings, and the method that actually produced today's usable
contacts -- reading a company's own team page -- was done by hand in a browser.
The router in `sourcing` chose between the first two by asking which *could*
apply, not which returns anyone, so it reported full coverage of 222 companies
while EDGAR yielded people for none of them.

This runs the sources, keeps what they return, and is honest about the rest.

  FINRA        registered broker-dealers. Real, current, and free, but it
               carries no job title, so it says who works there and not what
               they do.
  Team page    the org chart sites publish name and title together, which is
               the piece FINRA is missing. Crowd-sourced, so a title can be
               stale -- treated as a lead, never as a fact.

Ranking is by desk match first and seniority second. Seniority matters in a
specific direction: the target is a VP or Director, because a Managing Director
or Head rarely answers an unsolicited note and an Analyst cannot speak for the
desk. That preference came from being told it directly, twice.

Anyone written to in the last year is dropped before ranking, not after --
see recent_contacts. Addresses come from company_formats, which declines to
build one when the evidence conflicts, so a candidate can be real and still
arrive with no address. That is reported rather than papered over with a guess.
"""

import re

import company_formats
import recent_contacts

# Title bands, most wanted first. The regexes read the title as published.
# "president" must not swallow "Vice President". A VP is the target band;
# matching the substring put Joseph Baron (VP, FIG) and Eric Rakusin (SVP,
# Institutional Sales Trading) in the same bucket as a global co-head, which
# is the exact mistake this ranking exists to prevent.
_SENIOR = re.compile(
    r"(?i)(\b(managing director|chief|head of|global head|"
    r"partner|vice chairman|founder|c[ete]o)\b"
    r"|(?<!vice )(?<!senior vice )(?<!executive vice )\bpresident\b"
    r"|\bmd\b)")
_TARGET = re.compile(
    r"(?i)\b(vice president|svp|evp|vp|director|principal|senior manager|"
    r"portfolio manager|senior|lead)\b")
_JUNIOR = re.compile(r"(?i)\b(analyst|associate|intern|assistant|coordinator)\b")

BAND_TARGET, BAND_SENIOR, BAND_JUNIOR, BAND_UNKNOWN = (
    "vp/director", "too senior", "too junior", "unknown")


def band(title: str) -> str:
    """Which seniority band a published title falls in."""
    title = title or ""
    # Order matters: "Director of Public Equity Research, Portfolio Manager"
    # is a target, while "Managing Director, Head of X" is not -- so the
    # senior test runs first and wins outright.
    # A title carrying both ("Managing Director, Head of X" vs "Senior Vice
    # President of Y") is decided by the senior test, which is written so that
    # the Vice President forms never reach it.
    if _SENIOR.search(title):
        return BAND_SENIOR
    if _TARGET.search(title):
        return BAND_TARGET
    if _JUNIOR.search(title):
        return BAND_JUNIOR
    return BAND_UNKNOWN


_BAND_RANK = {BAND_TARGET: 0, BAND_UNKNOWN: 1, BAND_SENIOR: 2, BAND_JUNIOR: 3}


def _desk_score(title: str, role: str) -> int:
    """Shared meaningful words between a published title and the job applied to."""
    stop = {"analyst", "associate", "senior", "junior", "the", "and", "of",
            "vice", "president", "director", "manager", "global", "group"}
    want = {w for w in re.findall(r"[a-z]+", (role or "").lower())
            if len(w) > 3 and w not in stop}
    have = {w for w in re.findall(r"[a-z]+", (title or "").lower())
            if len(w) > 3 and w not in stop}
    return len(want & have)


def rank(candidates: list[dict], role: str = "") -> list[dict]:
    """Best person first: desk match, then seniority band, then title present."""
    for c in candidates:
        c["band"] = band(c.get("title", ""))
        c["desk_score"] = _desk_score(c.get("title", ""), role)
    return sorted(
        candidates,
        key=lambda c: (-c["desk_score"], _BAND_RANK.get(c["band"], 9),
                       not c.get("title")))


def with_addresses(candidates: list[dict], domain: str) -> list[dict]:
    """Attach an address where the domain's convention is actually known."""
    pattern, why = company_formats.pattern_for(domain)
    for c in candidates:
        c["email"] = (company_formats.address_for(c["name"], domain, pattern)
                      if pattern else "")
        c["address_basis"] = why
    return candidates


def find(company: str, role: str = "", domain: str = "", state: str = "NY",
         limit: int = 12) -> dict:
    """{candidates, dropped, notes} for one company.

    `dropped` is the people who exist but cannot be written to -- already
    contacted, or no address. Keeping them visible is the point: an empty
    result and a result full of unreachable people are different problems.
    """
    notes, found = [], []

    try:
        import people_finder
        firms = people_finder.find_firm(company)
        for firm_id, firm_name in firms[:3]:
            registered = people_finder.people_at_firm(firm_id, state=state,
                                                      limit=120)
            if not registered:
                continue
            notes.append(f"FINRA {firm_name}: {len(registered)} in {state}")
            for person in people_finder.mid_level(registered):
                found.append({"name": person["name"].title(), "title": "",
                              "source": f"FINRA ({firm_name})",
                              "years": person.get("years")})
            break
        if not firms:
            notes.append("not a registered broker-dealer — FINRA has nobody")
    except Exception as exc:
        notes.append(f"FINRA unavailable: {type(exc).__name__}")

    seen, unique = set(), []
    for person in found:
        key = person["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(person)

    if domain:
        unique = with_addresses(unique, domain)

    fresh, already = recent_contacts.filter_contacts(unique)
    if already:
        notes.append(f"{len(already)} already written to in the last year")

    ranked = rank(fresh, role)
    unreachable = [c for c in ranked if not c.get("email")]
    reachable = [c for c in ranked if c.get("email")]
    return {
        "candidates": reachable[:limit],
        "dropped": already + unreachable[:limit],
        "notes": notes,
    }


def run(company: str, role: str = "", domain: str = "", state: str = "NY") -> None:
    result = find(company, role, domain, state)
    for note in result["notes"]:
        print(f"  · {note}")
    print()
    if not result["candidates"] and not result["dropped"]:
        print(f"Nobody found at {company}. FINRA only registers broker-dealers, "
              f"so a bank's\ncorporate side, a utility or a private company will "
              f"not appear — those need\nthe company's own team page.")
        return
    if result["candidates"]:
        print(f"Write to these ({len(result['candidates'])}):")
        for c in result["candidates"]:
            years = f"{c['years']}y" if c.get("years") is not None else "  "
            print(f"  {years:>4}  {c['name'][:26]:26s} {c.get('email','')[:34]:34s}"
                  f" {c.get('title','') or c['band']}")
    if result["dropped"]:
        print(f"\nFound but not writable ({len(result['dropped'])}):")
        for c in result["dropped"][:8]:
            why = (f"emailed {c['last_emailed']}" if c.get("last_emailed")
                   else c.get("address_basis", "no address"))
            print(f"        {c['name'][:26]:26s} {why[:52]}")
