"""Email conventions per company, and how to build an address from a name.

Two sources feed this. `address_book` learns a domain's pattern from mail that
actually arrived, which is the strongest evidence there is. For a company you
have never written to there is no history, and the convention has to be looked
up — every directory service publishes them, usually with an adoption rate.

A looked-up convention is a good guess, not a fact. Adoption is rarely 100%,
so an address built here is labelled as inferred everywhere it surfaces, and
`address_book.check` still gets the last word on whether it has bounced.
"""

import json
import os
import re
import unicodedata

REGISTRY = os.path.join("logs", "company_formats.json")

# pattern name -> how to build the local part from (first, last)
BUILDERS = {
    "first.last": lambda f, l: f"{f}.{l}",
    "flast": lambda f, l: f"{f[0]}{l}",
    "firstlast": lambda f, l: f"{f}{l}",
    "first_last": lambda f, l: f"{f}_{l}",
    "lastf": lambda f, l: f"{l}{f[0]}",
    "first": lambda f, l: f,
    "last": lambda f, l: l,
    "f.last": lambda f, l: f"{f[0]}.{l}",
    "firstl": lambda f, l: f"{f}{l[0]}",
}


def _ascii(text: str) -> str:
    """Strips accents: Nájera is najera in an address."""
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def load(path: str = REGISTRY) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(data: dict, path: str = REGISTRY) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


def address_for(name: str, domain: str, pattern: str) -> str:
    """"Peter Chang", "fitch.com", "first.last" -> peter.chang@fitch.com."""
    build = BUILDERS.get(pattern)
    if not build:
        return ""
    words = [w for w in re.split(r"\s+", _ascii(name).strip()) if w]
    words = [re.sub(r"[^A-Za-z-]", "", w) for w in words]
    words = [w for w in words if len(w) > 1]
    if len(words) < 2:
        return ""
    first, last = words[0].lower(), words[-1].lower()
    return f"{build(first, last)}@{domain}".replace("--", "-")


# Domains that are people's personal mail, not a company convention. An
# address here says nothing about how any employer builds addresses.
PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "aol.com", "me.com", "protonmail.com", "nyu.edu",
}


def _person(local: str) -> tuple[str, str]:
    """Collapse a local part to (first initial, surname).

    diana.luk, dluk and diana_luk are one person, and counting them as three
    pieces of evidence made a third of all domains look like they mixed
    conventions. They mostly do not: 34 of the 36 apparent conflicts were the
    same person stored under several guesses, because "delivered" here means
    "sent and no bounce came back", so an address this tool guessed lands in
    the evidence pile next to one that was actually confirmed.
    """
    parts = [w for w in re.split(r"[._-]", local.lower()) if w]
    if len(parts) >= 2:
        return parts[0][0], parts[-1]
    whole = parts[0] if parts else ""
    return (whole[0], whole[1:]) if len(whole) > 2 else (whole, whole)


_SHAPE_TO_PATTERN = {
    "first.last": "first.last",
    "first_last": "first_last",
    "initial+last or surname": "flast",
}


def learned_pattern(domain: str, book: dict | None = None) -> tuple[str, str]:
    """(pattern, evidence) from delivered mail, one vote per person.

    Returns ("", reason) rather than guessing when the evidence conflicts —
    an address built on a coin flip is worse than admitting there is none,
    because it costs a bounce and the reader never sees the email.
    """
    import address_book

    if domain in PERSONAL_DOMAINS:
        return "", f"{domain} is personal mail, not a company convention"

    book = address_book.load() if book is None else book
    entry = book.get(domain) or {}
    delivered = entry.get("delivered") or []
    if not delivered:
        return "", "no delivered mail at this domain"

    # One vote per person, so a single contact written to three ways cannot
    # outvote three different colleagues who all use the same shape.
    by_person: dict = {}
    for address in delivered:
        local = address.split("@")[0]
        by_person.setdefault(_person(local), set()).add(
            address_book.classify(local))

    votes: dict = {}
    for shapes in by_person.values():
        if len(shapes) != 1:
            continue  # this person is on record two ways; they settle nothing
        pattern = _SHAPE_TO_PATTERN.get(next(iter(shapes)))
        if pattern:
            votes[pattern] = votes.get(pattern, 0) + 1

    if not votes:
        return "", f"{len(delivered)} address(es) on record, none conclusive"
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return "", (f"conflicting conventions at {domain} "
                    f"({', '.join(p for p, _ in ranked)})")
    pattern, count = ranked[0]
    people = len([s for s in by_person.values() if len(s) == 1])
    return pattern, f"{count} of {people} people at {domain} use {pattern}"


def pattern_for(domain: str, registry: dict | None = None) -> tuple[str, str]:
    """(pattern, where it came from). Delivered history beats a lookup."""
    learned, note = learned_pattern(domain)
    if learned:
        return learned, note
    registry = load() if registry is None else registry
    entry = (registry or {}).get(domain)
    if entry:
        return entry["pattern"], entry.get("source", "looked up") + " (inferred)"
    return "", note or "unknown"
