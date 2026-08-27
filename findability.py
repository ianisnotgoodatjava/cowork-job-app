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
    if not slug or _is_vendor_domain(domain):
        return False
    label = _domain_label(domain)
    if not label:
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
