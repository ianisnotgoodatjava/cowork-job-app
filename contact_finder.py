"""Finds real people at a company from your own mailbox.

Web research finds strangers one company at a time and costs a search per
company. Your sent mail already contains hundreds of real, spelled-correctly,
already-delivered addresses at exactly the kinds of firms you apply to — and
an address you have written to before is worth more than one inferred from a
naming convention, because it either bounced or it did not.

Everything here is a lookup against mail you already have. No web, no API.
"""

import datetime
import json
import os
import re

import gmail_client
from company_match import company_in_sender
from config import ME_RE

# Addresses that are a system, not a person.
_ROBOT_RE = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|noreply|automated|auto-?reply|"
    r"mailer|postmaster|bounce|notification|notifications|alerts?|info|"
    r"support|help|admin|webmaster|careers?|jobs?|recruiting|recruitment|"
    r"talent|hr|people|apply|applications?|survey|news|marketing|events?|"
    r"invitations?|messages?-noreply|updates?)([.+-]|$)", re.I)

_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _addresses(header: str) -> list[tuple[str, str]]:
    """[(display name, address)] from a From/To/Cc header."""
    import email.utils
    out = []
    for name, addr in email.utils.getaddresses([header or ""]):
        if not addr or "@" not in addr:
            continue
        out.append((name.strip().strip('"'), addr.strip()))
    return out


# Display names that give away a system even when the address does not:
# "Nomura HR SuccessFactors" writes from a perfectly human-looking address.
_ROBOT_NAME_RE = re.compile(
    r"do.?not.?reply|no.?reply|workday|successfactors|taleo|icims|greenhouse|"
    r"talent acquisition|recruit(ing|ment)|human resources|\bhr\b|careers?|"
    r"system|automated|notification", re.I)


def _is_person(addr: str, name: str = "") -> bool:
    local = addr.split("@")[0]
    if _ROBOT_RE.match(local):
        return False
    if name and _ROBOT_NAME_RE.search(name):
        return False
    # An address that is all-digits or a long opaque token is a system id.
    if re.fullmatch(r"[0-9._-]+", local) or len(local) > 40:
        return False
    return True


# Credentials people append to their display name. "CAIA Jane Doe" is
# not what you call someone in a greeting.
_CREDENTIALS_RE = re.compile(
    r"\b(CAIA|CFA|CPA|MBA|PhD|MPA|MSc|MS|MD|JD|FRM|CAMS|ACA|ACCA|PMP|"
    r"Esq|Jr|Sr|III|II)\b\.?", re.I)


def _pretty(name: str) -> str:
    name = (name or "").strip().strip('"')
    if "," in name and name.count(",") == 1:
        last, first = [p.strip() for p in name.split(",")]
        if last and first:
            name = f"{first} {last}"
    name = _CREDENTIALS_RE.sub("", name)
    return re.sub(r"\s{2,}", " ", name).strip(" ,.-")


_PARTICLES = {"de", "del", "della", "van", "von", "der", "den", "di", "da",
              "la", "le", "du", "st", "mc", "mac", "bin", "al"}


def name_from_address(addr: str) -> str:
    """"jane.doe@example.com" -> "Jane Doe".

    Most corporate mail carries no display name, but a first.last local part
    states the name outright. Only dotted (or underscored) locals qualify:
    "jlin@" could be J Lin or Jlin, and guessing wrong in the greeting
    is worse than not writing at all. Single-letter middle initials are
    dropped rather than greeted.
    """
    local = addr.split("@")[0]
    parts = [p for p in re.split(r"[._-]+", local) if p]
    words = [p for p in parts if p.isalpha() and len(p) > 1]
    if len(words) < 2:
        return ""
    first, last = words[0], words[-1]
    if len(first) < 2 or len(last) < 2:
        return ""

    def cap(w):
        return w.capitalize() if w.lower() not in _PARTICLES else w.lower()

    return f"{cap(first)} {cap(last)}"


def has_real_name(person: dict) -> bool:
    """True when we know what to actually call them.

    An address alone is not a name. "jsmith@example.com" would be greeted as
    "Dear Jsmith", which reads as a mail merge and wastes the contact — the
    one thing a cold email cannot afford in its first two words.
    """
    parts = [w for w in (person.get("name") or "").split() if w]
    if len(parts) >= 2 and all(
            re.fullmatch(r"[A-Za-z][A-Za-z'\-\.]{1,}", w) for w in parts[:2]):
        return True
    return bool(name_from_address(person.get("email", "")))


def best_name(person: dict) -> str:
    """What to put after "Dear". The header's name wins; the address fills in."""
    if has_real_name(person):
        parts = [w for w in (person.get("name") or "").split() if w]
        if len(parts) >= 2 and all(
                re.fullmatch(r"[A-Za-z][A-Za-z'\-\.]{1,}", w)
                for w in parts[:2]):
            return person["name"]
        return name_from_address(person.get("email", ""))
    return ""


CACHE = os.path.join("logs", "contacts_harvest.json")


def _load_cache() -> dict:
    try:
        with open(CACHE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(people: dict, newest_day: str) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump({"people": people, "through": newest_day}, f, indent=1)


def harvest(days_back: int = 700, max_results: int = 6000,
            include_drafts: bool = True, refresh: bool = False) -> dict:
    """domain-ish key -> [{name, email, seen, wrote_to}] for every human found.

    Reads both directions: people who wrote to you, and people you wrote to.

    Incremental. Re-reading the whole 700-day mailbox on every run meant ~7,000
    message fetches against a quota that allows roughly fifty a second; one
    pass lost 6,866 of them and found nobody at all. The set of people in old
    mail does not change, so old mail is read once and cached, and later runs
    only fetch what has arrived since.
    """
    cached = {} if refresh else _load_cache()
    known = cached.get("people") or {}
    through = cached.get("through")
    window = days_back
    if known and through:
        try:
            age = (datetime.date.today()
                   - datetime.date.fromisoformat(through)).days
            # A couple of days of overlap, so nothing falls through the seam.
            window = max(2, age + 2)
            print(f"  (cached: {len(known)} address(es) through {through}; "
                  f"reading the last {window} day(s))")
        except ValueError:
            window = days_back
    days_back = window
    # Drafts are read too, but kept separable. A contact sitting in an unsent
    # draft was researched at some point and is a real lead; it is simply not
    # evidence that the address works, the way a delivered message is. Mixing
    # the two would let an inferred address look confirmed by nothing but
    # itself, so the distinction is carried through to the draft's header.
    msgs = gmail_client.search_messages(
        f"newer_than:{days_back}d -in:chats -in:drafts", include_body=False,
        max_results=max_results)
    drafted = gmail_client.search_messages(
        f"in:drafts newer_than:{days_back}d", include_body=False,
        max_results=1500) if include_drafts else []
    if drafted:
        print(f"  plus {len(drafted)} unsent draft(s)")
    print(f"  scanned {len(msgs)} message(s)")

    people: dict[str, dict] = {}
    for m in list(msgs) + list(drafted):
        from_draft = m in drafted if drafted else False
        outbound = bool(ME_RE.search(m.get("from", "")))
        headers = [(m.get("to", ""), True), (m.get("from", ""), False)]
        for header, is_recipient in headers:
            for name, addr in _addresses(header):
                if ME_RE.search(addr) or not _is_person(addr, name):
                    continue
                key = addr.lower()
                rec = people.setdefault(key, {
                    "email": addr, "name": "", "seen": 0,
                    "wrote_to": False, "replied": False, "delivered": False,
                })
                if not from_draft:
                    rec["delivered"] = True
                rec["seen"] += 1
                if name and not rec["name"]:
                    rec["name"] = _pretty(name)
                if outbound and is_recipient:
                    rec["wrote_to"] = True
                if not outbound and not is_recipient:
                    rec["replied"] = True

    # Merge onto the cache rather than replacing it: counters add up, and a
    # reply seen in either pass stays a reply.
    for key, rec in known.items():
        cur = people.get(key)
        if cur is None:
            people[key] = rec
            continue
        cur["seen"] += rec.get("seen", 0)
        cur["name"] = cur.get("name") or rec.get("name", "")
        for flag in ("wrote_to", "replied", "delivered"):
            cur[flag] = bool(cur.get(flag)) or bool(rec.get(flag))
    _save_cache(people, datetime.date.today().isoformat())
    return people


def for_company(company: str, people: dict) -> list[dict]:
    """The humans in `people` who work at `company`, best first."""
    hits = [p for p in people.values()
            if company_in_sender(company, p["email"])]
    # Someone who replied to you outranks someone you only wrote to, which
    # outranks an address that merely appeared in a thread.
    # A reply is the only real proof the address works and the person is
    # still there. Everything below it is a guess of Ian's own that nobody
    # answered, so it ranks far lower than the old ordering implied.
    hits.sort(key=lambda p: (p["replied"], p.get("delivered", False),
                             p["wrote_to"], p["seen"]), reverse=True)
    return hits
