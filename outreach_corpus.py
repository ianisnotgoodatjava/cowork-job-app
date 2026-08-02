"""Builds a structured corpus of your past sent outreach emails.

Each record keeps the fields that make one email retrievable later: who it
went to, at what company, about what team/subject area, and the body text
used as the drafting example.
"""

import json
import os
import re
from email.utils import parsedate_to_datetime

import gmail_client

CORPUS_FILE = "outreach_corpus.json"

NETWORKING_QUERY = (
    'in:sent ("reaching out" OR "quick question" OR "informational" OR '
    '"coffee chat" OR "would love to learn" OR "inquiring about" OR '
    '"your experience" OR "quick call" OR "picking your brain" OR referral)'
)

# "Recent NYU Graduate Inquiring about {AREA} at {COMPANY}"
_SUBJECT_RE = re.compile(
    r"inquiring about\s+(?P<area>.+?)\s+at\s+(?P<company>.+?)\s*$", re.I
)
_GREETING_RE = re.compile(r"^\s*(?:dear|hi|hello)\s+([A-Z][a-z]+)", re.I | re.M)
# Gmail quotes the prior message under these markers; everything after is
# someone else's writing, not a style example.
_QUOTE_RE = re.compile(r"\nOn .{0,80}wrote:|\n>+\s|\n-{2,}\s*Forwarded", re.S)


def _strip_quoted(body: str) -> str:
    m = _QUOTE_RE.search(body)
    return (body[: m.start()] if m else body).strip()


def build(max_results: int = 500, out_path: str = CORPUS_FILE) -> list[dict]:
    print(f"Searching Gmail: {NETWORKING_QUERY}\n")
    msgs = gmail_client.search_messages(
        NETWORKING_QUERY, max_results=max_results, include_body=True
    )
    print(f"Fetched {len(msgs)} sent emails. Parsing...")

    records = []
    skipped_short = 0
    for m in msgs:
        body = _strip_quoted(m.get("body", ""))
        # Very short bodies are one-line replies ("Thanks!"), not outreach
        # examples worth learning phrasing from.
        if len(body) < 200:
            skipped_short += 1
            continue

        subject = m.get("subject", "")
        area = company = ""
        sm = _SUBJECT_RE.search(subject)
        if sm:
            area = sm.group("area").strip()
            company = sm.group("company").strip()

        gm = _GREETING_RE.search(body)
        try:
            date = parsedate_to_datetime(m["date"]).date().isoformat()
        except Exception:
            date = ""

        records.append({
            "id": m["id"],
            "date": date,
            "to": m.get("to", ""),
            "subject": subject,
            "company": company,
            "area": area,
            "first_name": gm.group(1) if gm else "",
            "body": body,
            "is_follow_up": subject.lower().startswith("re:"),
        })

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=1)

    with_meta = sum(1 for r in records if r["company"] and r["area"])
    print(f"Kept {len(records)} outreach emails "
          f"({skipped_short} skipped as too short).")
    print(f"{with_meta} have a parsed company + subject area.")
    print(f"Saved to {out_path}")
    return records


def load(path: str = CORPUS_FILE) -> list[dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found — run `python cli.py build-outreach-corpus` first."
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)
