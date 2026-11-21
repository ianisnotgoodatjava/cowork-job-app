"""After an application goes in: is anyone at the firm a Taiwan connection?

A shared school or hometown turns a cold email into a warm one, and the
person most worth finding is the one nearest the role -- the same desk first,
then the same function, then anyone at the firm. This answers that once per
application, right after it lands on the sheet.

Two routes, like the rest of sourcing:

  links     always, free. A LinkedIn people search and a web query, built
            for this company and role, to open and browse yourself. LinkedIn
            is never fetched or automated (see linkedin_helper).
  research  with MODEL_API_KEY. One Muse web-search request per application
            that returns named people, each with the page that states their
            Taiwan connection. core/taiwan_signals checks every one, and a
            person whose "connection" is a surname, Mandarin, or a market
            they cover is refused with the reason -- not reported.

Read-only as far as anyone else is concerned: nothing is sent and the sheet
is not touched. Findings go to logs/, which is gitignored, so the names of
real people never reach the repository.
"""

import datetime
import json
import os
import re

from core import app_identity
from core import company_id
from core import taiwan_signals as signals
from core.date_utils import parse_date
from people import people_search
from sources import muse

STORE_FILE = os.path.join("logs", "taiwan_connections.json")
RAW_DIR = os.path.join("logs", "taiwan")

# The same firm, checked for another role this recently, is not re-billed by
# the scheduled sweep: who works there has not changed, only how near the
# role they sit. Asking by hand for a specific application still researches.
REUSE_DAYS = 30

SYSTEM = """You find people at one company who publicly state a connection to Taiwan, for a job applicant with a Taiwan background who wants to network with them about a role they just applied to.

WHAT COUNTS -- something the person published about themselves:
- a school in Taiwan: National Taiwan University, National Chengchi University, National Tsing Hua University, NYCU / National Chiao Tung, National Cheng Kung University, Taipei American School, and so on
- a Taiwanese employer (TSMC, Fubon, Cathay, CTBC, Yuanta, KGI, E.Sun, ...) or a role in a Taiwan office
- membership of a Taiwanese professional or student group: Taiwanese American Professionals, a university Taiwanese Student Association, Monte Jade, NATPA, ...
- Taiwanese (Hokkien) listed among their languages
- calling themselves Taiwanese or Taiwanese-American, or a hometown in Taiwan

WHAT DOES NOT COUNT -- never report anyone on these grounds:
- their name. Chen, Lin, Wang, Huang, Chang and the rest are shared across mainland China, Hong Kong, Singapore, Malaysia and the diaspora. Never guess anyone's nationality or ethnicity from a name or a photo.
- speaking Mandarin or Chinese
- covering Taiwan as a market or sector, e.g. an analyst on Taiwan semiconductors
- a school with a namesake elsewhere: Tsinghua University (Beijing) is not National Tsing Hua (Hsinchu); Sun Yat-sen University (Guangzhou) is not National Sun Yat-sen (Kaohsiung); a bare "NTU" is often Nanyang in Singapore

WHO TO LOOK FOR
People who work at the company NOW -- not alumni of it -- nearest the role first: the same team or desk, then the same function, then anyone at the firm. Any seniority; a peer is as useful as a director. Look at public profiles surfaced by web search, the firm's own pages, alumni-association and student-group pages, conference bios and press releases.

NEVER invent a person or a connection. If you are not sure they currently work there, or the connection is not stated on a page you can cite, leave them out. Finding nobody is a fine answer; a made-up person is not.

OUTPUT -- exactly this, nothing else. One block per person, at most 6, nearest the role first:

=== PERSON ===
NAME: <full name>
TITLE: <current title at the company>
TEAM: <team, desk or division, or UNKNOWN>
LOCATION: <city, or UNKNOWN>
PROFILE_URL: <public profile URL, or UNKNOWN>
TAIWAN_EVIDENCE: <the connection in the source's own words, e.g. "B.B.A., National Taiwan University">
TAIWAN_SOURCE: <URL of the page that states it>
RELEVANCE: <same team | same function | elsewhere at the firm>
WHY_RELEVANT: <one line: how their work relates to the role>

If you find nobody, output exactly:
=== NONE ===
<one line on where you looked>"""

_NONE_RE = re.compile(r"^\s*===\s*NONE\s*===\s*$(.*)", re.M | re.S)

# A staffing agency is not the employer: its people are recruiters, not the
# desk. Same rule auto_draft applies before spending research on a row.
_AGENCY_RE = re.compile(
    r"staffing|recruit|talent partners|search partners|\bagency\b", re.I)


# --- the store ---------------------------------------------------------------

def _load() -> dict:
    try:
        with open(STORE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(store: dict) -> None:
    os.makedirs(os.path.dirname(STORE_FILE), exist_ok=True)
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=1, ensure_ascii=False)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "x"


# --- deciding ------------------------------------------------------------------

def _desk_score(person: dict, role: str) -> int:
    """Desk words shared between their title/team and the role applied to."""
    want = set(signals.role_terms(role, limit=99))
    have = set(signals.role_terms(
        f"{person.get('title', '')} {person.get('team', '')}", limit=99))
    return len(want & have)


def screen(people: list[dict], role: str) -> tuple[list[dict], list[dict]]:
    """(report these, refused with a reason), the first ranked.

    Pure: takes what the model returned and decides, so it can be tested
    without a model or a network.
    """
    kept, refused, seen = [], [], set()
    for person in people:
        why = signals.check(person)
        key = (person.get("name") or "").strip().lower()
        if not why and key in seen:
            why = "listed twice"
        if why:
            refused.append({**person, "refused": why})
            continue
        seen.add(key)
        kept.append({**person,
                     "kind": signals.kind(person["evidence"]),
                     "band": people_search.band(person.get("title", ""))})
    kept.sort(key=lambda p: signals.sort_key(p, _desk_score(p, role)))
    return kept, refused


def research(company: str, role: str, location: str = "",
             skills: str = "") -> dict:
    """One paid web-search request, checked. Never raises for a bad answer."""
    prompt = (f"Company: {company}\n"
              f"Role applied to: {role or 'unknown'}\n"
              f"Location of the role: {location or 'unknown'}\n"
              + (f"What the role involves: {skills[:500]}\n" if skills else "")
              + "\nFind people currently at this company with a stated "
                "Taiwan connection, nearest this role first.")
    try:
        text = muse.respond(SYSTEM, prompt, web_search=True, effort="medium")
    except RuntimeError as exc:
        return {"people": [], "refused": [], "error": str(exc)}

    kept, refused = screen(signals.parse(text), role)
    result = {"people": kept, "refused": refused}
    none = _NONE_RE.search(text)
    if none and not kept:
        result["note"] = none.group(1).strip()[:200]

    os.makedirs(RAW_DIR, exist_ok=True)
    raw = os.path.join(RAW_DIR, f"{datetime.date.today():%Y%m%d}_"
                                f"{_slug(company)}_{_slug(role)}.txt")
    with open(raw, "w", encoding="utf-8") as f:
        f.write(text)
    result["raw"] = raw
    return result


def links(company: str, role: str) -> dict:
    query = signals.xray_query(company, role)
    return {"linkedin": signals.linkedin_urls(company, role),
            "web": signals.google_url(query), "query": query}


def for_application(company: str, role: str, location: str = "",
                    skills: str = "", redo: bool = False,
                    use_model: bool = True) -> dict:
    """The answer for one application: cached, freshly researched, or links."""
    key = app_identity.key(company, role)
    store = _load()
    cached = store.get(key)
    if cached and not redo:
        return {**cached, "cached": True, **links(company, role)}

    result = {"company": company, "role": role, **links(company, role)}
    if not (use_model and muse.available()):
        result["skipped"] = ("no MODEL_API_KEY" if use_model
                             else "links only")
        return result

    found = research(company, role, location, skills)
    if found.get("error"):
        # Not stored: a failed request is retried next time, not remembered.
        return {**result, **found}
    record = {"company": company, "role": role,
              "company_key": company_id.key(company),
              "checked": f"{datetime.date.today():%Y-%m-%d}",
              **{k: found[k] for k in ("people", "refused", "note", "raw")
                 if k in found}}
    store[key] = record
    _save(store)
    return {**result, **record}


# --- showing -------------------------------------------------------------------

def show(result: dict, verbose: bool = True) -> None:
    company, role = result.get("company", ""), result.get("role", "")
    print(f"{company}" + (f"  —  {role}" if role else ""))
    if result.get("cached"):
        print(f"  (checked {result.get('checked')}; --redo to research again)")

    people = result.get("people") or []
    if people:
        print(f"\n  Taiwan connections ({len(people)}), nearest the role first:")
        for p in people:
            print(f"\n  · {p['name']} — {p.get('title') or 'title unknown'}"
                  + (f", {p['team']}" if p.get("team") else ""))
            print(f"      {p['relevance']}; {p['kind']}: {p['evidence'][:90]}")
            if p.get("why_relevant"):
                print(f"      {p['why_relevant'][:100]}")
            print(f"      source: {p['source']}")
            if p.get("profile_url") and p["profile_url"] != p["source"]:
                print(f"      profile: {p['profile_url']}")
    elif "checked" in result:
        print("\n  Nobody with a stated Taiwan connection found"
              + (f" — {result['note']}" if result.get("note") else "."))
    elif result.get("error"):
        print(f"\n  research failed: {result['error'][:120]}")
    elif result.get("skipped"):
        print(f"\n  research skipped ({result['skipped']}) — the searches "
              f"below are free.")

    refused = result.get("refused") or []
    if refused:
        reasons = {}
        for p in refused:
            reasons[p["refused"]] = reasons.get(p["refused"], 0) + 1
        print(f"\n  refused {len(refused)}: " + "; ".join(
            f"{n}× {why}" for why, n in sorted(reasons.items(),
                                               key=lambda x: -x[1])))

    if verbose or not people:
        print("\n  Browse it yourself:")
        for label, url in result.get("linkedin", []):
            print(f"    LinkedIn, {label}:\n      {url}")
        print(f"    Web:\n      {result.get('web', '')}")
    print()


def run(company: str = "", role: str = "", row: int | None = None,
        links_only: bool = False, redo: bool = False) -> None:
    """One application, by sheet row or by name."""
    location = skills = ""
    if row:
        from store import sheets
        r = sheets.get_row(row)
        company = (r.get("Company") or "").strip()
        role = (r.get("Position") or "").strip()
        location = r.get("Location") or r.get("location") or ""
        skills = r.get("Required Skills & Experience") or ""
    if not company:
        raise SystemExit("Give --row, or --company (and ideally --role).")
    show(for_application(company, role, location, skills, redo=redo,
                         use_model=not links_only))


# --- after applying ------------------------------------------------------------

def pending(rows: list[dict], days_back: int = 14, limit: int = 5,
            store: dict | None = None,
            today: datetime.date | None = None) -> list[dict]:
    """Recent open applications not yet checked, one per company.

    Pure: the sweep's choice, separated so it can be tested on plain rows.
    """
    store = _load() if store is None else store
    today = today or datetime.date.today()
    cutoff = today - datetime.timedelta(days=days_back)
    reuse_cutoff = f"{today - datetime.timedelta(days=REUSE_DAYS):%Y-%m-%d}"
    recent_firms = {v.get("company_key") for v in store.values()
                    if (v.get("checked") or "") >= reuse_cutoff}

    def applied(r):
        return parse_date(r.get("Applied Draes") or r.get("Applied Date")
                          or "") or datetime.date.min

    out, seen = [], set()
    for r in sorted(rows, key=applied, reverse=True):
        company = (r.get("Company") or "").strip()
        if not company or applied(r) < cutoff:
            continue
        if "reject" in (r.get("Status") or "").lower():
            continue
        if _AGENCY_RE.search(company):
            continue
        firm = company_id.key(company)
        if firm in seen or firm in recent_firms:
            continue
        if any(k in store for k in app_identity.keys_for(
                company, r.get("Position", ""))):
            continue
        seen.add(firm)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def sweep(days_back: int = 14, limit: int = 5, dry_run: bool = False) -> None:
    """The scheduled half: check each new application once."""
    from store import sheets
    todo = pending(sheets.list_rows(), days_back=days_back, limit=limit)
    if not todo:
        print("  nothing new to check")
        return
    if dry_run or not muse.available():
        print(f"  {len(todo)} new application(s) to check"
              + ("" if muse.available() else
                 " — no MODEL_API_KEY, so only the free searches:"))
        for r in todo:
            print(f"    row {r.get('row_number', '?'):>4}  {r['Company'][:30]:30s}"
                  f" {(r.get('Position') or '')[:40]}")
        print("  searches for one: python cli.py taiwan-connections "
              "--row N --links-only")
        return
    for r in todo:
        company, role = r["Company"].strip(), (r.get("Position") or "").strip()
        result = for_application(
            company, role, r.get("Location") or r.get("location") or "",
            r.get("Required Skills & Experience") or "")
        people = result.get("people") or []
        line = (f"{len(people)} found" if "checked" in result
                else result.get("error", "failed")[:60])
        print(f"  {company[:30]:30s} {line}")
        for p in people[:3]:
            print(f"      {p['name'][:26]:26s} {p['relevance']:14s} "
                  f"{p['kind']}: {p['evidence'][:40]}")
    print("  details: python cli.py taiwan-connections --row N")
