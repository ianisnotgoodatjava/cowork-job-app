"""Ranks open applications by how cheaply a real contact can be found.

The outreach bottleneck is not writing the email — it is finding a named
person and an address that works. Treating all 366 open rows as equally
searchable is what makes that slow: some companies hand you a full contact
block in a press release, others are private shops with no public footprint
at all, and both were getting the same five minutes of searching.

This sorts them instead, so the effort goes where it converts:

  PUBLISHES   a rating agency or research house. Every rating action carries
              an analytical contact block — name, title, phone, address —
              and the piece itself is the hook. Both halves verifiable, no
              guessing. KBRA gave up delia.lawver@ and jerry.jurcisin@ this
              way, with the deals they worked on attached.
  KNOWN_ADDR  you have already had mail delivered to that domain, so
              address_book knows the real format. Only the person is missing.
  BOUNCED     you have written to that domain and every attempt came back.
              The format on record is wrong; needs a published address, not
              another guess at the pattern.
  COLD        no history and no public contact convention. Genuinely
              expensive — worth doing last, or not at all.

Read-only: reads the sheet and the address_book cache, writes nothing.
"""

import json
import os

import sheets
import status as status_mod
from company_match import _core, _is_vendor_domain, normalize
from date_utils import parse_date

CACHE = os.path.join("logs", "address_patterns.json")

# Firms whose analytical or research staff are published as a matter of
# course. For these the contact block *is* the source — no inference from a
# name pattern, and the published piece doubles as the hook.
_PUBLISHERS = (
    "kbra", "kroll bond", "fitch", "moody", "standard & poor", "s&p",
    "morningstar", "dbrs", "creditsights", "9fin", "covenant review",
    "leveraged commentary", "lcd", "bloomberg intelligence",
    "wolfe research", "bernstein", "new street research", "redburn",
    "cfra", "value line", "argus research", "sifma",
)


def _domain_label(domain: str) -> str:
    """'1823.partners' -> '1823'; 'websterbank.com' -> 'websterbank'."""
    return domain.split(".")[0].lower()


def _slug(company: str) -> str:
    return (_core(company or "").replace(" ", "")
            or normalize(company or "").replace(" ", ""))


def _domain_matches(slug: str, domain: str) -> bool:
    """Whether this company slug really owns this domain.

    Deliberately strict. A loose substring test tied "New Fortress Energy"
    to fortress.com, "Insight Global" to insightinvestment.com and "U.S.
    Bank" to us.mufg.jp — three different companies, three addresses that
    would have gone to strangers. Equality, or one fully containing the
    other with enough characters left that the overlap cannot be an
    accident.
    """
    # A slug this short carries no identity. "U.S. Bank" reduces to "us"
    # once "Bank" is stripped as a descriptor, and "us" matches the label of
    # us.mufg.jp exactly — a different bank on another continent. Anything
    # under three characters is thrown away rather than matched.
    if not slug or len(slug) < 3 or _is_vendor_domain(domain):
        return False
    label = _domain_label(domain)
    if not label or len(label) < 3:
        return False
    if slug == label:
        return True
    # Prefix, not containment. Plain containment matched "New Fortress
    # Energy" to fortress.com, because "fortress" sits inside
    # "newfortressenergy" — two unrelated firms. A shortened domain drops
    # words off the *end* ("greenskiescleanenergy" -> greenskies.com), never
    # off the front. The length floor stops "bp" claiming bpost.com.
    shorter, longer = sorted((slug, label), key=len)
    return len(shorter) >= 7 and longer.startswith(shorter)


def _load_patterns() -> dict:
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def classify(row: dict, patterns: dict) -> tuple[str, str]:
    """Returns (tier, evidence) for one application."""
    company = (row.get("Company") or "").strip()
    lower = company.lower()

    for name in _PUBLISHERS:
        if name in lower:
            return "PUBLISHES", "analytical/research contacts are published"

    slug = _slug(company)
    for domain, hist in patterns.items():
        if not _domain_matches(slug, domain):
            continue
        delivered = hist.get("delivered") or []
        bounced = hist.get("bounced") or []
        if delivered:
            return "KNOWN_ADDR", f"{delivered[0]} delivered"
        if bounced:
            return "BOUNCED", f"{len(bounced)} bounced, no address that works"
    return "COLD", "no history, no published contacts"


ORDER = ["PUBLISHES", "KNOWN_ADDR", "BOUNCED", "COLD"]


def rank(days_back: int | None = None) -> dict[str, list]:
    """Groups open applications by tier, newest application first."""
    import datetime

    patterns = _load_patterns()
    rows = [r for r in sheets.list_rows()
            if status_mod.is_open(r.get("Status", ""))]

    if days_back:
        cutoff = datetime.date.today() - datetime.timedelta(days=days_back)
        rows = [r for r in rows
                if (parse_date(r.get("Applied Draes", "")) or datetime.date.min)
                >= cutoff]

    buckets: dict[str, list] = {t: [] for t in ORDER}
    for row in rows:
        tier, why = classify(row, patterns)
        buckets[tier].append((row, why))

    for tier in buckets:
        buckets[tier].sort(
            key=lambda p: parse_date(p[0].get("Applied Draes", ""))
            or datetime.date.min,
            reverse=True)
    return buckets


def _draft_index() -> tuple[set[str], list[str]]:
    """(recipient domain labels, draft subjects) for every existing draft."""
    import time

    from googleapiclient.discovery import build
    from google_auth import get_credentials

    svc = build("gmail", "v1", credentials=get_credentials())

    def call(fn):
        for _ in range(6):
            try:
                return fn()
            except Exception as exc:
                if "403" in str(exc) or "rateLimit" in str(exc):
                    time.sleep(15)
                    continue
                raise
        raise RuntimeError("Gmail rate limit")

    domains: set[str] = set()
    subjects: list[str] = []
    token = None
    while True:
        page = call(lambda: svc.users().drafts().list(
            userId="me", maxResults=100, pageToken=token).execute())
        for draft in page.get("drafts", []):
            full = call(lambda: svc.users().drafts().get(
                userId="me", id=draft["id"], format="metadata").execute())
            head = {h["name"]: h["value"]
                    for h in full["message"]["payload"]["headers"]}
            to = (head.get("To") or "").lower()
            if "@" in to:
                domains.add(_domain_label(to.split("@")[-1].strip("> ")))
            subjects.append((head.get("Subject") or "").lower())
        token = page.get("nextPageToken")
        if not token:
            break
    return domains, subjects


def coverage(days_back: int | None = None) -> tuple[list, list]:
    """Splits open applications into (has a draft, has none).

    Matched two ways because neither is sufficient alone. Domain matching
    misses a shortened name — the slug for "Fitch Group" is "fitch", which
    never matches fitchratings.com under the strict prefix rule and reported
    a real Harold Chen draft as missing. The subject line carries the
    company verbatim ("... at Fitch Ratings"), so it catches what the domain
    cannot.
    """
    import datetime

    patterns = _load_patterns()
    rows = [r for r in sheets.list_rows()
            if status_mod.is_open(r.get("Status", ""))]
    if days_back:
        cutoff = datetime.date.today() - datetime.timedelta(days=days_back)
        rows = [r for r in rows
                if (parse_date(r.get("Applied Draes", "")) or datetime.date.min)
                >= cutoff]

    domains, subjects = _draft_index()
    have, missing = [], []
    for row in rows:
        company = (row.get("Company") or "").strip()
        slug = _slug(company)
        hit = any(_domain_matches(slug, d) or slug == d for d in domains)
        if not hit and len(company) >= 4:
            hit = any(company.lower() in s for s in subjects)
        (have if hit else missing).append((row, classify(row, patterns)[0]))
    return have, missing


def run_coverage(days_back: int | None = None) -> None:
    have, missing = coverage(days_back)
    total = len(have) + len(missing)
    window = f" from the last {days_back} days" if days_back else ""
    print(f"{total} open application(s){window}: "
          f"{len(have)} with a draft, {len(missing)} without\n")
    if not missing:
        print("Every open application has a draft.")
        return

    by_tier: dict[str, list] = {}
    for row, tier in missing:
        by_tier.setdefault(tier, []).append(row)
    for tier in ORDER:
        items = by_tier.get(tier) or []
        if not items:
            continue
        print(f"### no draft — {tier}  ({len(items)})")
        for row in items:
            print(f"  row {row['row_number']:4d} | {row['Company'][:30]:30s} | "
                  f"{row['Position'][:34]}")
        print()
    cheap = sum(len(by_tier.get(t) or []) for t in ("PUBLISHES", "KNOWN_ADDR"))
    print(f"{cheap} of the {len(missing)} gaps have a verifiable contact path.")
    print("  ./venv/bin/python cli.py auto-draft --limit "
          f"{max(cheap, 1)} --write-drafts")


def run(days_back: int | None = None, show: int = 12) -> None:
    buckets = rank(days_back)
    total = sum(len(v) for v in buckets.values())
    window = f" applied in the last {days_back} days" if days_back else ""
    print(f"{total} open application(s){window}\n")

    for tier in ORDER:
        items = buckets[tier]
        if not items:
            continue
        print(f"### {tier}  ({len(items)})")
        for row, why in items[:show]:
            print(f"  row {row['row_number']:4d} | "
                  f"{row.get('Applied Draes', ''):10s} | "
                  f"{row['Company'][:28]:28s} | {why}")
        if len(items) > show:
            print(f"  ... and {len(items) - show} more")
        print()

    cheap = len(buckets["PUBLISHES"]) + len(buckets["KNOWN_ADDR"])
    print(f"{cheap} of {total} have a verifiable contact path already.")
    print("Work those first: PUBLISHES needs no address guess at all, and")
    print("KNOWN_ADDR only needs the person — the format is already proven.")
