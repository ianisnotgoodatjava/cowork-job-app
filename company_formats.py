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
# Endings a domain may add to a shortened company name and still be the same
# employer. Anything outside this list is treated as a different word, and a
# different word means a different company.
_CORPORATE_SUFFIXES = {
    "co", "inc", "corp", "grp", "group", "llc", "lp", "us", "usa", "na",
    "bank", "cap", "am", "ib", "sec",
}

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


# ---------------------------------------------------------------------------
# Company -> mail domain.
#
# Everything above answers "how does this domain build addresses". Nothing
# answered "which domain does this employer use", which is the step that has to
# come first and was being done by hand every time.
#
# Delivered mail is the strongest evidence and is checked first. The fallback
# is the domain the application confirmation came from, with the applicant
# tracking systems filtered out -- an email from myworkday.com or icims.com
# says which vendor the employer buys from, not where its staff read mail.

def _slugify(text: str) -> str:
    from company_match import _core, normalize
    return re.sub(r"[^a-z0-9]", "",
                  (_core(text or "") or normalize(text or "")).lower())


def _same_employer(label: str, want: str) -> bool:
    """Whether a domain label and a company slug name the same employer.

    One must contain the other from the start. Comparing only the first few
    characters matched AlphaSights to alphacapital.online -- two firms that
    share a prefix and nothing else -- and a wrong domain is worse than none,
    because every address built on it bounces.
    """
    if not label or not want or len(label) < 3:
        return False
    return label == want or (min(len(label), len(want)) >= 5
                             and (want.startswith(label)
                                  or label.startswith(want)))


def domain_for(company: str, book: dict | None = None) -> tuple[str, str]:
    """(mail domain, evidence) for an employer, or ("", why not)."""
    import address_book
    from company_match import _is_vendor_domain

    want = _slugify(company)
    if len(want) < 3:
        return "", f"company name {company!r} too short to match on"

    book = address_book.load() if book is None else book
    best, best_note = "", ""
    for domain, entry in book.items():
        if domain in PERSONAL_DOMAINS or not (entry.get("delivered") or []):
            continue
        # Every label, not just the first: the employer's name sits in the
        # middle of us.mufg.jp, and reading only "us" found nothing.
        labels = [_slugify(part) for part in domain.split(".")[:-1]]
        labels = [l for l in labels if len(l) >= 3]
        if not labels:
            continue
        hit = any(l == want or want.startswith(l) or l.startswith(want)
                  for l in labels)
        if not hit:
            # Shared prefix, for a domain that abbreviates the name:
            # rockco.com for Rockefeller Capital Management. Six characters
            # keeps "rockco"/"rockefeller" while refusing "us"/"usbank" and
            # other collisions that a shorter run would wave through.
            hit = any(len(l) >= 6 and len(want) >= 6 and l[:6] == want[:6]
                      for l in labels)
        if not hit:
            # Last resort, and the weakest rule here: a domain that contracts
            # the name past recognition -- rockco.com for Rockefeller Capital
            # Management. Only the employer's first word is compared, and only
            # against domains already in the address book, so the candidates
            # are limited to places mail has actually been sent.
            # rockco.com for Rockefeller Capital Management: the label is the
            # first word cut short plus a corporate suffix. The suffix list is
            # what keeps this honest -- without it "alpha" matched AlphaSights
            # to alphacapital.online, a different firm entirely, because the
            # two merely start alike.
            head = _slugify((company or "").split()[0]) if company.split() else ""
            hit = any(len(head) >= 6 and len(l) >= 6 and l.startswith(head[:4])
                      and l[4:] in _CORPORATE_SUFFIXES
                      for l in labels)
        if hit:
            n = len(entry["delivered"])
            if not best or n > len(book[best]["delivered"]):
                best, best_note = domain, f"{n} delivered to {domain}"
    if best:
        return best, best_note

    # Drafts count as evidence too. A domain reached this far once already --
    # somebody researched it -- and Karbone is the case in point: eleven drafts
    # to @karbone.com and not one sent, so delivery history knows nothing.
    try:
        import gmail_client
        for draft in gmail_client.draft_headers():
            match = re.search(r"@([\w.-]+\.\w+)", draft["to"])
            if not match:
                continue
            domain = match.group(1).lower()
            if domain in PERSONAL_DOMAINS:
                continue
            labels = [_slugify(part) for part in domain.split(".")[:-1]]
            if any(_same_employer(l, want) for l in labels):
                return domain, f"an existing draft is addressed to {domain}"
    except Exception:
        pass

    try:
        import gmail_client
        msgs = gmail_client.search_messages(
            f'newer_than:400d "{company}"', max_results=25)
    except Exception:
        return "", "no delivered mail, and Gmail unavailable"

    for msg in msgs:
        match = re.search(r"@([\w.-]+\.\w+)", msg.get("from", ""))
        if not match:
            continue
        domain = match.group(1).lower()
        if domain in PERSONAL_DOMAINS or _is_vendor_domain(domain):
            continue
        # Reduce an HR or careers subdomain to the domain staff actually use:
        # the Nomura confirmation comes from ghrs.nomura.com, and nobody on the
        # desk reads mail there. Only applied on this path -- a delivered
        # address at us.mufg.jp is proof that the subdomain itself is right.
        parts = domain.split(".")
        if len(parts) > 2:
            domain = ".".join(parts[-2:])
        labels = [_slugify(part) for part in domain.split(".")[:-1]]
        if any(_same_employer(l, want) for l in labels):
            return domain, f"sender of mail about {company}"
    return "", f"no mail domain found for {company}"
