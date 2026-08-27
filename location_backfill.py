"""Fills the empty `location` column.

205 of 430 rows have no location, which makes the sheet hard to scan and hides
the fact that some applications are not even in cities you would move to.

Two sources, both already in hand — no new fetching where it can be avoided:

  1. The job title itself. Postings routinely carry the city:
     "Analyst, US Strategy (Charlotte)", "Business Analyst - NYC",
     "Structured Finance - ABS (New York, 49419)".
  2. The application confirmation email, which usually states the location
     near the role name.

A city is only written when it is recognisably a city. Anything ambiguous is
left blank on purpose: a wrong location is worse than an empty one, because
you would filter on it.
"""

import re

import gmail_client
import sheets
from company_match import company_in_sender, company_mentioned
from link_finder import CONFIRMATION_PHRASES

# Cities that actually appear in this search, plus their canonical spelling.
CITIES = {
    "nyc": "New York, NY", "new york": "New York, NY",
    "new york city": "New York, NY", "manhattan": "New York, NY",
    "chicago": "Chicago, IL", "boston": "Boston, MA",
    "philadelphia": "Philadelphia, PA", "philly": "Philadelphia, PA",
    "charlotte": "Charlotte, NC", "atlanta": "Atlanta, GA",
    "dallas": "Dallas, TX", "houston": "Houston, TX",
    "austin": "Austin, TX", "denver": "Denver, CO",
    "san francisco": "San Francisco, CA", "los angeles": "Los Angeles, CA",
    "san jose": "San Jose, CA", "seattle": "Seattle, WA",
    "miami": "Miami, FL", "tampa": "Tampa, FL",
    "minneapolis": "Minneapolis, MN", "stamford": "Stamford, CT",
    "greenwich": "Greenwich, CT", "hartford": "Hartford, CT",
    "jersey city": "Jersey City, NJ", "princeton": "Princeton, NJ",
    "newark": "Newark, NJ", "baltimore": "Baltimore, MD",
    "washington": "Washington, DC", "washington dc": "Washington, DC",
    "pittsburgh": "Pittsburgh, PA", "cleveland": "Cleveland, OH",
    "columbus": "Columbus, OH", "detroit": "Detroit, MI",
    "phoenix": "Phoenix, AZ", "salt lake city": "Salt Lake City, UT",
    "london": "London, UK", "toronto": "Toronto, ON",
    "singapore": "Singapore", "hong kong": "Hong Kong",
    "taipei": "Taipei, Taiwan", "tokyo": "Tokyo, Japan",
    "remote": "Remote",
}

# Longest names first so "new york city" wins over "new york".
_CITY_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(c) for c in CITIES), key=len,
                             reverse=True)) + r")\b", re.I)


# A city inside an organisation's name is branding, not a location. "London
# Stock Exchange Group" put LSEG's New York sales role in London.
_ORG_AFTER_CITY_RE = re.compile(
    r"^\s*(stock exchange|mercantile|board of trade|exchange|university|college|life|mutual|"
    r"community|partners|capital|group|bank|trust|school|institute|"
    r"hospital|health|times|post|city f\.?c)\b", re.I)


def from_text(text: str) -> str:
    """Canonical city named in `text`, or ''."""
    for m in _CITY_RE.finditer(text or ""):
        if _ORG_AFTER_CITY_RE.match((text or "")[m.end():m.end() + 24]):
            continue
        return CITIES[m.group(1).lower()]
    return ""


def from_title(position: str) -> str:
    """City carried in the job title — usually in brackets or after a dash."""
    # Only look at the decorated parts, so a role like "Analyst, US Strategy"
    # does not pick up a city mentioned elsewhere in a long title.
    chunks = re.findall(r"[(\[]([^)\]]{2,60})[)\]]", position or "")
    tail = re.split(r"\s[-–]\s", position or "")
    if len(tail) > 1:
        chunks.append(tail[-1])
    # Titles also append the city after a comma — "Risk Analyst, Boston",
    # "Benefits Analyst, New York City". Missing these let the email pass
    # overwrite a Boston role with New York, which the title had said plainly.
    comma_parts = [c.strip() for c in (position or "").split(",")[1:]]
    chunks.extend(comma_parts)
    for chunk in chunks:
        city = from_text(chunk)
        if city:
            return city
    return ""


def run(dry_run: bool = True, days_back: int = 520, use_email: bool = True) -> None:
    rows = sheets.list_rows()
    todo = [r for r in rows if not (r.get("location") or "").strip()]
    print(f"{len(todo)} row(s) missing a location (of {len(rows)})\n", flush=True)

    found, sources = [], {"title": 0, "email": 0}
    for r in todo:
        city = from_title(r.get("Position", ""))
        if city:
            found.append((r, city, "title"))
            sources["title"] += 1

    done = {r["row_number"] for r, _, _ in found}

    if use_email:
        query = f"newer_than:{days_back}d (" + \
                " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES) + ")"
        print("Reading application confirmation emails...", flush=True)
        msgs = gmail_client.search_messages(query, include_body=True,
                                            max_results=900)
        print(f"  {len(msgs)} email(s)\n", flush=True)

        for r in todo:
            if r["row_number"] in done:
                continue
            for m in msgs:
                hay = f"{m['subject']} {m.get('snippet', '')}".lower()
                if not (company_mentioned(r["Company"], hay)
                        or company_in_sender(r["Company"], m.get("from", ""))):
                    continue
                city = from_text(m.get("subject", "")) or \
                    from_text((m.get("snippet") or "")[:400])
                if city:
                    found.append((r, city, "email"))
                    sources["email"] += 1
                    break

    for r, city, src in sorted(found, key=lambda x: x[0]["row_number"]):
        print(f"  row {r['row_number']:3d} [{src:5s}] {r['Company'][:22]:22s} | "
              f"{r['Position'][:38]:38s} -> {city}")

    print(f"\n{'Would fill' if dry_run else 'Filled'} {len(found)} of "
          f"{len(todo)} — {sources['title']} from the title, "
          f"{sources['email']} from email.")
    print("The rest are left blank on purpose: nothing in the row names a "
          "city, and\na guessed location is worse than an empty one.")

    if dry_run:
        print("\n(dry-run — nothing written)")
        return
    for r, city, _ in found:
        sheets.update_row(r["row_number"], {"location": city})
    print(f"Wrote {len(found)} location cell(s).")
