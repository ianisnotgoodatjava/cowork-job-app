"""For every live application, says whether anyone there has actually been reached.

You are applying faster than you are networking, and the two lists have drifted
apart: some rows have five contacts and a reply, others have none. This ranks
the open applications by how much a contact would help, so the next hour of
outreach goes where there is no coverage at all rather than to a company you
have already emailed six times.

Coverage per row, from your sent mail:
  * NONE      - nobody at that domain has ever been written to
  * BOUNCED   - you tried, but every message came back undelivered
  * SENT      - delivered, no reply yet
  * REPLIED   - a human answered; this is a live thread, not a cold start

Read-only. It reports; it never drafts, sends or writes to the sheet.
"""

import collections
import re

import gmail_client
import interview_signals
import sheets
import status as status_mod
from company_match import _core, normalize
from config import ME_RE
from date_utils import parse_date

_BOUNCE_RE = re.compile(
    r"(mailer-daemon|postmaster|undeliverable|returned mail|delivery status|"
    r"could not be delivered|address rejected|invalid recipient|no such user)", re.I)


def _slug(company: str) -> str:
    return _core(company or "").replace(" ", "") or normalize(company or "").replace(" ", "")


def run(days_back: int = 520, limit: int = 700) -> None:
    rows = [r for r in sheets.list_rows() if status_mod.is_open(r.get("Status", ""))]
    print(f"{len(rows)} open application(s)\n", flush=True)

    print("Reading your sent mail...", flush=True)
    sent = gmail_client.search_messages(
        f"in:sent newer_than:{days_back}d", max_results=limit)
    print(f"{len(sent)} sent message(s)\n", flush=True)

    # domain -> {addresses tried, threads}
    contacted = collections.defaultdict(lambda: {"to": set(), "threads": set()})
    for m in sent:
        to = (m.get("to") or "").lower()
        for addr in re.findall(r"[\w.+-]+@[\w.-]+", to):
            dom = addr.split("@")[1]
            contacted[dom]["to"].add(addr)
            contacted[dom]["threads"].add(m.get("threadId") or m["id"])

    # Which of those domains bounced, and which answered.
    print("Checking which ones landed...", flush=True)
    inbound = gmail_client.search_messages(
        f"newer_than:{days_back}d -in:sent", max_results=limit * 2)
    bounced_threads, replied_threads = set(), set()
    for m in inbound:
        tid = m.get("threadId") or m["id"]
        frm = m.get("from", "")
        if _BOUNCE_RE.search(frm) or _BOUNCE_RE.search(m.get("subject", "")):
            bounced_threads.add(tid)
        elif not ME_RE.search(frm) and not interview_signals.is_automated(
                frm, m.get("subject", "")):
            replied_threads.add(tid)

    buckets = collections.Counter()
    report = []
    for r in rows:
        # Match on both the shortened core and the full name. Requiring 4+
        # characters was dropping every short company: "ABI Research" cores
        # down to "abi", so a thread where Paul Schell had already replied was
        # reported as "nobody contacted".
        candidates = {_slug(r["Company"]), normalize(r["Company"]).replace(" ", "")}
        candidates = {c for c in candidates if len(c) >= 3}
        hit = None
        for dom, info in contacted.items():
            label = dom.split(".")[0].replace("-", "")
            if any(c == label or (len(c) >= 4 and (c in label or label in c))
                   for c in candidates):
                hit = info
                break
        if not hit:
            state, detail = "NONE", ""
        else:
            threads = hit["threads"]
            if threads & replied_threads:
                state = "REPLIED"
            elif threads and threads <= bounced_threads:
                state = "BOUNCED"
            else:
                state = "SENT"
            detail = f"{len(hit['to'])} address(es): " + \
                     ", ".join(sorted(hit["to"])[:2])
        buckets[state] += 1
        report.append((state, parse_date(r.get("Applied Draes", "")), r, detail))

    order = {"NONE": 0, "BOUNCED": 1, "SENT": 2, "REPLIED": 3}
    import datetime
    report.sort(key=lambda x: (order[x[0]], -(x[1] or datetime.date.min).toordinal()))

    print("=" * 72)
    for state in ("NONE", "BOUNCED", "SENT", "REPLIED"):
        group = [x for x in report if x[0] == state]
        if not group:
            continue
        headline = {
            "NONE": "no contact attempted — these are where outreach buys the most",
            "BOUNCED": "you tried and every message bounced — find a working address",
            "SENT": "delivered, awaiting reply",
            "REPLIED": "live thread — follow these up before starting anything cold",
        }[state]
        print(f"\n### {state}  ({len(group)})  {headline}")
        for _, d, r, detail in group[:25]:
            print(f"  row {r['row_number']:3d} | {str(d or '?'):10s} | "
                  f"{r['Company'][:26]:26s} | {r['Position'][:34]}")
            if detail:
                print(f"          {detail[:78]}")
        if len(group) > 25:
            print(f"  ... and {len(group) - 25} more")

    print("\n" + "=" * 72)
    print(" | ".join(f"{k}: {v}" for k, v in buckets.most_common()))
