"""What counts as a Taiwan connection, and how to search for one.

The question asked after an application goes in is "is there anyone at this
firm I share a Taiwan background with, near the role I applied to?" A shared
school or hometown is the warmest cold email there is -- but only if the
connection is real. Writing "as a fellow Taiwanese" to someone who is not is
worse than writing nothing, for the same reason a fabricated hook is.

So a connection is something the person published about *themselves*:

  school       a Taiwanese university or a school in Taiwan
  employer     a Taiwanese firm, or a Taipei/Hsinchu office
  association  a Taiwanese professional or student group
  language     Taiwanese (Hokkien) listed as a language
  place        lives, grew up, or is based in a Taiwanese city

and a surname is not one. Chen, Lin and Wang are as common in Beijing,
Singapore and Kuala Lumpur as in Taipei, and guessing nationality from a name
is both unreliable and the kind of profiling nobody wants done to them. The
model is told this, and `check()` refuses an inference from a name even if it
slips through. Mandarin alone is refused for the same reason; so is an
analyst who *covers* Taiwan, which is a market, not a background.

Pure logic: no network. The research that fills these in is
people/taiwan_connections.py.
"""

import re
from urllib.parse import quote_plus

from core import linkedin_helper

SCHOOL, EMPLOYER, ASSOCIATION, LANGUAGE, PLACE = (
    "school", "employer", "association", "language", "place")

# Strongest first. An association or a school is chosen and specific; a city
# can be where someone was posted for a year.
KIND_ORDER = (ASSOCIATION, SCHOOL, LANGUAGE, EMPLOYER, PLACE)

# Cities and counties. Matched as words, so "Taiwan" counts and so does
# "Hsinchu Science Park", but nothing is read out of a person's name.
_PLACE_RE = re.compile(
    r"\b(taiwan|taipei|new taipei|hsinchu|kaohsiung|taichung|tainan|taoyuan|"
    r"keelung|chiayi|hualien|yilan|pingtung|changhua|miaoli|nantou|yunlin|"
    r"taitung|penghu|kinmen|formosa)\b", re.I)

# Taiwanese universities and schools whose names do not say "Taiwan". Several
# are spelled to exclude a namesake elsewhere: "National Tsing Hua" is in
# Hsinchu, "Tsinghua University" is in Beijing; "National Sun Yat-sen" is in
# Kaohsiung, "Sun Yat-sen University" is in Guangzhou. A bare "NTU" is
# Nanyang in Singapore as often as it is National Taiwan, so it is not here.
_SCHOOL_RE = re.compile(
    r"\b(national chengchi|nccu|national tsing ?hua|nthu|chiao tung|nctu|"
    r"yang ?ming|nycu|cheng kung|ncku|national sun yat-?sen|nsysu|"
    r"chung hsing|national central university|national chung cheng|"
    r"fu jen|tamkang|tunghai|feng chia|chang gung|yuan ze|chung yuan|"
    r"soochow university.{0,12}taipei|ming chuan|shih hsin|"
    r"chinese culture university|kang chiao|morrison academy|"
    r"chien ?kuo|jianguo|ntust|taiwan tech)\b", re.I)

# Taiwanese employers whose names do not say "Taiwan". Kept to firms with no
# common namesake: "Evergreen", "Giant" and "HTC" mean other things too.
_EMPLOYER_RE = re.compile(
    r"\b(tsmc|hon hai|foxconn|mediatek|asus|asustek|acer|quanta|pegatron|"
    r"wistron|compal|inventec|delta electronics|umc|united microelectronics|"
    r"ase group|largan|cathay (financial|life|united)|fubon|ctbc|yuanta|"
    r"kgi|e\.?sun|taishin|sinopac|mega (financial|bank|international)|"
    r"chunghwa|first financial holding|hua nan|chang hwa bank|"
    r"shin kong|uni-?president|eva air|china airlines)\b", re.I)

_ASSOCIATION_RE = re.compile(
    r"\b(taiwanese|tap-?ny|natpa|monte jade|tacl|fapa|"
    r"taiwan(ese)? (student|graduate|american|alumni)\w*)\b", re.I)

_LANGUAGE_RE = re.compile(r"\b(hokkien|min ?nan|taiwanese \(?(language|"
                          r"native|fluent|hokkien)\)?)\b", re.I)

# Evidence that is really a guess about the person, not something they said.
_INFERENCE_RE = re.compile(
    r"\b(surname|last name|family name|first name|name (is|sounds|suggests)|"
    r"sounds? taiwanese|likely|probably|possibly|presumably|appears to be|"
    r"may be|might be|ethnic\w*|looks)\b", re.I)

# Taiwan as a market someone covers, not a place they come from.
_COVERAGE_RE = re.compile(
    r"\b(cover(s|ing|age)?|exposure|sector|market(s)?|semis|semiconductor "
    r"(stocks|names)|equities|portfolio|fund)\b", re.I)

# Words in a job title that say nothing about the desk.
_ROLE_STOP = {
    "analyst", "associate", "senior", "junior", "the", "and", "for", "with",
    "vice", "president", "director", "manager", "global", "group", "lead",
    "head", "officer", "chief", "specialist", "consultant", "professional",
    "executive", "business", "corporate", "general", "regional", "americas",
    "intern", "internship", "summer", "full", "time", "level", "entry",
    "york", "remote", "hybrid", "program", "role", "team",
}


def kind(evidence: str) -> str | None:
    """The strongest kind of connection the evidence states, or None."""
    text = evidence or ""
    found = set()
    if _ASSOCIATION_RE.search(text):
        found.add(ASSOCIATION)
    if _SCHOOL_RE.search(text) or (
            _PLACE_RE.search(text) and re.search(
                r"\b(universit|college|school|academy|institute|b\.?a\b|"
                r"b\.?s\b|bba|bachelor|master|m\.?s\b|mba|ph\.?d)", text, re.I)):
        found.add(SCHOOL)
    if _LANGUAGE_RE.search(text):
        found.add(LANGUAGE)
    if _EMPLOYER_RE.search(text):
        found.add(EMPLOYER)
    if _PLACE_RE.search(text):
        found.add(PLACE)
    for k in KIND_ORDER:
        if k in found:
            return k
    return None


def check(person: dict) -> str:
    """Why this person cannot be reported, or "" if they can.

    Every refusal carries a reason, so a run that finds nobody says whether
    nobody exists or whether the model offered guesses.
    """
    name = (person.get("name") or "").strip()
    if not name or name.upper() == "UNKNOWN" or len(name.split()) < 2:
        return "no full name"
    evidence = (person.get("evidence") or "").strip()
    if not evidence or evidence.upper() == "UNKNOWN":
        return "no stated Taiwan connection"
    if _INFERENCE_RE.search(evidence):
        return "connection inferred, not stated"
    found = kind(evidence)
    if not found:
        return "evidence names no Taiwanese school, employer, group or place"
    # "Covers Taiwan and Korea semiconductors" names a place and is still
    # not a background. A coverage word with only a place behind it fails;
    # a school or association alongside it still passes.
    if found == PLACE and _COVERAGE_RE.search(evidence):
        return "Taiwan is a market they cover, not their background"
    source = (person.get("source") or "").strip()
    if not source.lower().startswith("http"):
        return "no source URL for the connection"
    return ""


def role_terms(role: str, limit: int = 3) -> list[str]:
    """The words in a job title that say which desk it is."""
    words = []
    for word in re.findall(r"[a-z&]+", (role or "").lower()):
        if len(word) > 3 and word not in _ROLE_STOP and word not in words:
            words.append(word)
    return words[:limit]


# The terms a human-run search uses. Short on purpose: Google stops reading a
# query at 32 words, and these few cover most profiles that state a link.
_SEARCH_TERMS = ('"Taiwan"', '"Taipei"', '"Taiwanese"',
                 '"National Taiwan University"', '"National Chengchi"',
                 '"Tsing Hua"', '"Chiao Tung"', '"Cheng Kung"')


def linkedin_urls(company: str, role: str = "") -> list[tuple[str, str]]:
    """[(label, url)] LinkedIn people searches to open by hand.

    LinkedIn is browsed, never fetched -- see linkedin_helper. The narrowed
    search comes first because it is the one worth opening; the wide one is
    for a firm where nobody on the desk turns up.
    """
    taiwan = " OR ".join(_SEARCH_TERMS)
    terms = role_terms(role)
    out = []
    if terms:
        desk = " OR ".join(terms)
        out.append(("near the role", linkedin_helper.people_search_url(
            f'"{company}"', f"AND ({taiwan}) AND ({desk})")))
    out.append(("anywhere at the firm", linkedin_helper.people_search_url(
        f'"{company}"', f"AND ({taiwan})")))
    return out


def xray_query(company: str, role: str = "") -> str:
    """A web-search query for public profiles with a stated Taiwan link."""
    taiwan = " OR ".join(_SEARCH_TERMS)
    terms = role_terms(role)
    desk = f" ({' OR '.join(terms)})" if terms else ""
    return f'site:linkedin.com/in "{company}" ({taiwan}){desk}'


def google_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}"


# --- reading the research back ---------------------------------------------

_BLOCK_RE = re.compile(r"^===\s*PERSON\s*===\s*$", re.M)
_LINE_RE = re.compile(r"^\s*([A-Z_]+)\s*:\s*(.*?)\s*$")

# The model's field names, mapped onto the keys the rest of the code uses.
_FIELDS = {
    "NAME": "name", "TITLE": "title", "TEAM": "team", "LOCATION": "location",
    "PROFILE_URL": "profile_url", "TAIWAN_EVIDENCE": "evidence",
    "TAIWAN_SOURCE": "source", "RELEVANCE": "relevance",
    "WHY_RELEVANT": "why_relevant",
}

SAME_TEAM, SAME_FUNCTION, ELSEWHERE = (
    "same team", "same function", "elsewhere at the firm")
RELEVANCE_ORDER = (SAME_TEAM, SAME_FUNCTION, ELSEWHERE)


def _relevance(text: str) -> str:
    text = (text or "").lower()
    if "team" in text or "desk" in text:
        return SAME_TEAM
    if "function" in text or "adjacent" in text:
        return SAME_FUNCTION
    return ELSEWHERE


def parse(text: str) -> list[dict]:
    """People blocks out of the delimited response.

    Delimited text rather than a schema, like auto_draft: this runs
    unattended, and a format that degrades to readable text is worth more
    than one that fails to parse silently.
    """
    people = []
    for block in _BLOCK_RE.split(text or "")[1:]:
        person = {}
        for line in block.splitlines():
            m = _LINE_RE.match(line)
            if m and m.group(1) in _FIELDS:
                value = m.group(2)
                person[_FIELDS[m.group(1)]] = (
                    "" if value.upper() in ("UNKNOWN", "N/A", "NONE") else value)
        if person:
            person["relevance"] = _relevance(person.get("relevance", ""))
            people.append(person)
    return people


def sort_key(person: dict, desk_score: int = 0) -> tuple:
    """Nearest the role first, then the strongest connection.

    Relevance outranks the kind of connection: a fellow Taipei native on the
    desk is worth more than an NTU alum in another division. Seniority is
    deliberately not a filter here, unlike people_search -- a peer who
    shares a background is the likeliest reply of all.
    """
    rel = RELEVANCE_ORDER.index(person.get("relevance", ELSEWHERE))
    k = person.get("kind")
    strength = KIND_ORDER.index(k) if k in KIND_ORDER else len(KIND_ORDER)
    return (rel, -desk_score, strength)
