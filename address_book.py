"""Learns each company's email address format from what actually got delivered.

The single biggest waste in this search is not the reply rate — it is the
bounce rate. Roughly a third of the outreach never arrived: ~90 emails that
cost the same effort as the ones that landed and had exactly zero chance.
At a 9.8% reply rate those bounces are about nine conversations that never
happened.

Every bounce is also a labelled example nobody was using. Gmail already
records which addresses were delivered and which came back, so the format each
domain uses is sitting in the mailbox waiting to be counted:

    websterbank.com   sBenfante@ delivered, Steven.Benfante@ bounced
    abiresearch.com   Schell@    delivered, PSchell@         bounced
    kbra.com          brian.ford@ published

This turns that history into a per-domain pattern with evidence, so a guessed
address can be checked before it is used rather than after it fails.

Read-only over Gmail. Writes a cache to logs/, nothing else.
"""

import collections
import json
import os
import re

import gmail_client
from config import ME_RE

CACHE = os.path.join("logs", "address_patterns.json")

_BOUNCE_RE = re.compile(
    r"(mailer-daemon|postmaster|undeliverable|returned mail|delivery status|"
    r"could not be delivered|address rejected|invalid recipient|no such user)", re.I)

_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def classify(local: str) -> str:
    """Which shape an address local-part uses."""
    if "." in local:
        return "first.last"
    if "_" in local:
        return "first_last"
    # Heuristic: a single leading initial followed by a surname.
    if re.fullmatch(r"[a-z][a-z'-]{2,}", local, re.I):
        return "initial+last or surname"
    return "other"


def learn(days_back: int = 520, limit: int = 800) -> dict:
    """Returns {domain: {"delivered": [...], "bounced": [...]}} from your mail."""
    sent = gmail_client.search_messages(
        f"in:sent newer_than:{days_back}d", max_results=limit)
    inbound = gmail_client.search_messages(
        f"newer_than:{days_back}d -in:sent", max_results=limit * 2)

    bounced_threads = {
        m.get("threadId") or m["id"] for m in inbound
        if _BOUNCE_RE.search(m.get("from", "") + " " + m.get("subject", ""))
    }

    domains: dict[str, dict] = collections.defaultdict(
        lambda: {"delivered": set(), "bounced": set()})
    for m in sent:
        if not ME_RE.search(m.get("from", "")):
            continue
        tid = m.get("threadId") or m["id"]
        for addr in _ADDR_RE.findall((m.get("to") or "").lower()):
            domain = addr.split("@")[1]
            bucket = "bounced" if tid in bounced_threads else "delivered"
            domains[domain][bucket].add(addr)

    # An address that ever landed is delivered, even if a later thread bounced
    # for an unrelated reason.
    out = {}
    for domain, buckets in domains.items():
        delivered = buckets["delivered"]
        bounced = buckets["bounced"] - delivered
        if delivered or bounced:
            out[domain] = {"delivered": sorted(delivered),
                           "bounced": sorted(bounced)}
    return out


def pattern_for(domain: str, data: dict) -> tuple[str, str]:
    """Best-supported format for a domain, and the evidence for it."""
    entry = data.get(domain.lower())
    if not entry:
        return "", "no history for this domain"

    shapes = collections.Counter(
        classify(a.split("@")[0]) for a in entry["delivered"])
    if not shapes:
        return "", (f"only bounces on record "
                    f"({len(entry['bounced'])}): {', '.join(entry['bounced'][:3])}")

    shape, n = shapes.most_common(1)[0]
    evidence = f"{n} delivered ({', '.join(entry['delivered'][:2])})"
    if entry["bounced"]:
        evidence += f"; {len(entry['bounced'])} bounced"
    return shape, evidence


def check(address: str, data: dict | None = None) -> str:
    """One line on whether this address matches what has worked at that domain."""
    data = data if data is not None else load()
    if "@" not in address:
        return "not an address"
    local, domain = address.lower().split("@", 1)
    entry = data.get(domain, {})

    if address.lower() in {a.lower() for a in entry.get("delivered", [])}:
        return "SAFE — this exact address has been delivered to before"
    if address.lower() in {a.lower() for a in entry.get("bounced", [])}:
        return "BOUNCED BEFORE — this exact address failed; do not reuse it"

    shape, evidence = pattern_for(domain, data)
    if not shape:
        return f"UNKNOWN — {evidence}"
    mine = classify(local)
    if mine == shape:
        return f"matches the domain's pattern ({shape}); {evidence}"
    return (f"MISMATCH — you wrote {mine}, but {shape} is what lands here; "
            f"{evidence}")


def load(path: str = CACHE) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(data: dict, path: str = CACHE) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


def run(days_back: int = 520, refresh: bool = True, address: str = "") -> None:
    data = learn(days_back) if refresh else load()
    if refresh:
        save(data)

    if address:
        print(f"{address}\n  {check(address, data)}")
        return

    total_d = sum(len(v["delivered"]) for v in data.values())
    total_b = sum(len(v["bounced"]) for v in data.values())
    rate = total_b / (total_d + total_b) if (total_d + total_b) else 0
    print(f"{len(data)} domain(s) with history — {total_d} delivered, "
          f"{total_b} bounced ({rate:.0%})\n")

    risky = [(d, v) for d, v in data.items() if v["bounced"]]
    risky.sort(key=lambda kv: -len(kv[1]["bounced"]))
    print("=== domains where addresses have failed ===")
    for domain, v in risky[:20]:
        shape, evidence = pattern_for(domain, data)
        print(f"  {domain:32s} {len(v['bounced'])} bounced, "
              f"{len(v['delivered'])} delivered")
        print(f"      use: {shape or 'unknown'} — {evidence[:80]}")

    print("\nBefore sending to a guessed address:")
    print("  ./venv/bin/python cli.py addresses --check name@company.com")
