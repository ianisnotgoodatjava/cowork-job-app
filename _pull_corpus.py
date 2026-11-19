"""Pulls the confirmation-email corpus from Gmail once and caches it on disk.

The parser is the only part of this system with no offline test: every change
to its 40-odd regexes has been made blind, against whatever the next live run
happened to print. This freezes a real corpus so a change can be measured.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT = "tests/fixtures/confirmations.json"
BODY_CAP = 6000

from store import gmail_client
from sources.link_finder import CONFIRMATION_PHRASES

phrase_query = " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES)
query = f"newer_than:730d ({phrase_query}) -from:nyu.edu"
msgs = gmail_client.search_messages(query, max_results=4000, include_body=True)
print(f"pulled {len(msgs)}")

recs = [{
    "id": m.get("id", ""),
    "subject": m.get("subject", ""),
    "from": m.get("from", ""),
    "date": m.get("date", ""),
    "body": (m.get("body") or "")[:BODY_CAP],
} for m in msgs]
os.makedirs("tests/fixtures", exist_ok=True)
json.dump(recs, open(OUT, "w"), indent=1)
print(f"wrote {len(recs)} -> {OUT}")
