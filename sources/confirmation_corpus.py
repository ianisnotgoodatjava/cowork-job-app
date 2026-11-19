"""Freezes the mailbox's confirmation emails so the parser can be measured.

The parser decides what gets onto the sheet, and before this existed the only
way to exercise it was to run the live scan and read what it printed. So every
one of its rules was written against whatever that run happened to show, and
nobody could say whether a change had made things better or worse.

`pull()` takes one Gmail search — the same query `app_scanner.scan` uses — and
writes every message to `tests/fixtures/confirmations.json`. That costs about
thirteen minutes, because each body is its own request and the quota is 250
units a second. It is paid once. `report()` then measures the parser against
the file in about two seconds, and `tests/test_confirmation_parser.py` picks
the same file up when it is present.

The file is not committed: it is two years of one person's job search, and the
repository is public. It is in .gitignore, and the test skips without it.
"""

import json
import os

from core import confirmation_parser
from sources.link_finder import CONFIRMATION_PHRASES
from store import gmail_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "tests", "fixtures", "confirmations.json")

# Enough of each body for role extraction to see what it sees live. The role
# is near the top ("...for the position of Business Analyst"); the rest is
# signature, legal boilerplate and a tracking pixel.
BODY_CAP = 6000


def pull(days_back: int = 730, max_results: int = 4000) -> int:
    """Re-reads the mailbox and overwrites the frozen corpus."""
    phrases = " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES)
    query = f"newer_than:{days_back}d ({phrases}) -from:nyu.edu"
    print(f"Searching Gmail: {query}")
    print("Bodies are one request each, so this takes about a quarter of an "
          "hour. It only has to happen when you want a fresher corpus.")
    messages = gmail_client.search_messages(
        query, max_results=max_results, include_body=True)

    records = [{
        "id": m.get("id", ""),
        "subject": m.get("subject", ""),
        "from": m.get("from", ""),
        "date": m.get("date", ""),
        "body": (m.get("body") or "")[:BODY_CAP],
    } for m in messages]

    os.makedirs(os.path.dirname(CORPUS), exist_ok=True)
    with open(CORPUS, "w") as fh:
        json.dump(records, fh, indent=1)
    print(f"Wrote {len(records)} message(s) to {CORPUS}")
    return len(records)


def load() -> list[dict]:
    if not os.path.exists(CORPUS):
        return []
    with open(CORPUS) as fh:
        return json.load(fh)


def report(show: int = 12) -> dict:
    """Measures the parser against the frozen corpus and prints the breakdown."""
    messages = load()
    if not messages:
        print(f"No corpus at {CORPUS}.\n"
              f"Run:  ./venv/bin/python cli.py parser-accuracy --refresh")
        return {}

    result = confirmation_parser.measure(messages)
    readable = result["read"] + result["missed"]
    print(f"{result['total']} confirmation email(s) in the frozen corpus")
    print(f"  {result['excluded']:5d}  recorded no application "
          f"(job alerts, account signups) — correctly skipped")
    print(f"  {result['read']:5d}  read into a company and role")
    print(f"  {result['missed']:5d}  could not be read")
    if readable:
        print(f"\n  miss rate: {result['missed'] / readable * 100:.1f}%")

    print("\nwhich rule read it:")
    for name, count in sorted(result["by_parser"].items(), key=lambda kv: -kv[1]):
        print(f"  {count:5d}  {name}")

    print("\nwhy the rest were not read:")
    for reason, count in sorted(result["by_reason"].items(), key=lambda kv: -kv[1]):
        print(f"  {count:5d}  {reason:22s} "
              f"{confirmation_parser.REASONS[reason]}")

    # A row whose company is the ATS's own identifier is worth having and
    # needs a name. Naming them here is the shortest path to a TENANTS entry.
    slugged = sorted({r.company for _, r in result["results"]
                      if r.parser == "ats:slug"})
    if slugged:
        print(f"\n{len(slugged)} employer(s) known only by their vendor "
              f"identifier. Adding these to core.confirmation_parser.TENANTS, "
              f"spelled as the sheet spells them, is what fixes the name:")
        for company in slugged[:show]:
            print(f"    {company}")

    unread = [m for m in messages
              if not (p := confirmation_parser.parse(m))
              and p.reason not in ("noise", "not-an-application")]
    if unread:
        print(f"\nthe {len(unread)} that could not be read:")
        for m in unread[:show]:
            print(f"    {m.get('from', '')[:44]:46s} "
                  f"{m.get('subject', '')[:50]!r}")
        if len(unread) > show:
            print(f"    ... and {len(unread) - show} more")
    return result
