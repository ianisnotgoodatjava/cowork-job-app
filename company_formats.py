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


def pattern_for(domain: str, registry: dict | None = None) -> tuple[str, str]:
    """(pattern, where it came from). Delivered history beats a lookup."""
    registry = load() if registry is None else registry
    try:
        import address_book
        learned, note = address_book.pattern_for(domain, address_book.load())
        if learned and "first.last" in learned:
            return "first.last", f"learned from delivered mail: {note}"
        if learned and "initial+last" in learned:
            return "flast", f"learned from delivered mail: {note}"
    except Exception:
        pass
    entry = registry.get(domain)
    if entry:
        return entry["pattern"], entry.get("source", "looked up")
    return "", "unknown"
