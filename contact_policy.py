"""Learns who is actually worth emailing, from your own reply history.

You asked for reinforcement learning. RL is the wrong shape for this: it needs
thousands of episodes and a reward you can sample cheaply, and you send a few
hundred emails a year with weeks between a send and its outcome. Fitting RL
here would be decoration.

What *is* real is that every email you have ever sent is a labelled trial:
you picked a person, and they either replied or they did not. That is a
contextual-bandit problem, and with this much data the honest version of it is
plain counting — estimate the reply rate of each kind of contact, then pick
the arm with the best rate and enough evidence behind it.

So this reports, per feature, how often that choice actually earned a reply:

  * seniority of the person (head / senior / mid / HR)
  * whether the address was verified or guessed  -> bounce rate
  * whether you had also applied to a role there
  * length of the email

Read-only. It never sends, drafts, or writes to the sheet.
"""

import collections
import re

import gmail_client
import interview_signals
from config import ME_RE

# Seniority read from the address local-part and the subject line. Crude, but
# it is the only signal available *before* they reply — a title lifted from a
# reply signature only exists for people who replied, which is exactly the
# survivorship bias that would make the model useless for choosing.
_HR_RE = re.compile(r"\b(hr|recruit|talent|careers?|people|campus|university)\b", re.I)
_HEAD_RE = re.compile(r"\b(head|chief|cio|ceo|president|partner|managing director|md)\b", re.I)
_SENIOR_RE = re.compile(r"\b(senior|vp|vice president|director|principal|lead)\b", re.I)

_BOUNCE_RE = re.compile(
    r"(mailer-daemon|postmaster|undeliverable|returned mail|delivery status|"
    r"could not be delivered|address rejected|invalid recipient)", re.I)


def _bucket(address: str, subject: str) -> str:
    blob = f"{address} {subject}"
    if _HR_RE.search(address):
        return "HR / careers inbox"
    if _HEAD_RE.search(blob):
        return "head / MD"
    if _SENIOR_RE.search(blob):
        return "senior / VP"
    return "individual contributor"


def _address_style(address: str) -> str:
    local = address.split("@")[0]
    if "." in local:
        return "first.last"
    if re.fullmatch(r"[a-z]{1}[a-z'-]+", local, re.I):
        return "initial+last"
    return "other"


def analyse(days_back: int = 520, limit: int = 600) -> None:
    import cli

    query = f"in:sent newer_than:{days_back}d {cli.NETWORKING_KEYWORDS}"
    print(f"Searching Gmail: {query}\n", flush=True)
    sent = gmail_client.search_messages(query, max_results=limit,
                                        include_body=True)
    print(f"{len(sent)} outreach message(s); resolving outcomes...\n", flush=True)

    seen_threads, trials = set(), []
    for i, msg in enumerate(sent, 1):
        tid = msg.get("threadId") or msg["id"]
        if tid in seen_threads:
            continue
        seen_threads.add(tid)
        if len(seen_threads) % 40 == 0:
            print(f"  ...{len(seen_threads)} threads", flush=True)

        try:
            thread = gmail_client.get_thread(tid, include_body=False)
        except Exception:
            continue

        to = (msg.get("to") or "").split("<")[-1].strip("<> ").lower()
        if "@" not in to:
            continue

        # Length is only meaningful for the *opening* email, with quoted text
        # stripped. Measuring whichever sent message the search happened to
        # return produced a 93% "reply rate" for long emails — pure reverse
        # causation: a thread that got a reply has follow-ups quoting the
        # whole conversation, so replies make emails long, not the reverse.
        mine = [m for m in thread if ME_RE.search(m.get("from", ""))]
        is_opener = not mine or msg["id"] == min(
            mine, key=lambda m: m.get("date", ""))["id"]

        body = msg.get("body") or ""
        body = "\n".join(l for l in body.splitlines()
                         if not l.lstrip().startswith(">"))
        body = re.split(r"\bOn .{0,60}wrote:|-----Original Message-----",
                        body)[0].strip()

        replied = bounced = False
        for m in thread:
            frm = m.get("from", "")
            if ME_RE.search(frm):
                continue
            if _BOUNCE_RE.search(frm) or _BOUNCE_RE.search(m.get("subject", "")):
                bounced = True
            elif not interview_signals.is_automated(frm, m.get("subject", "")):
                replied = True

        trials.append({
            "to": to,
            "bucket": _bucket(to, msg.get("subject", "")),
            "style": _address_style(to),
            "replied": replied,
            "bounced": bounced,
            # None when this was not the opening email, so the length table
            # simply skips it rather than counting a quoted-back thread.
            "length": len(body) if is_opener else None,
        })

    if not trials:
        print("no outreach found in that window")
        return

    print(f"\n{'='*66}\n{len(trials)} outreach thread(s) with a known outcome\n{'='*66}")
    overall = sum(t["replied"] for t in trials) / len(trials)
    bounce = sum(t["bounced"] for t in trials) / len(trials)
    print(f"overall reply rate : {overall:5.1%}")
    print(f"overall bounce rate: {bounce:5.1%}  "
          f"({sum(t['bounced'] for t in trials)} never arrived)\n")

    def table(key: str, title: str) -> None:
        groups = collections.defaultdict(list)
        for t in trials:
            groups[t[key]].append(t)
        print(f"--- reply rate by {title} ---")
        rows = []
        for name, ts in groups.items():
            n = len(ts)
            r = sum(x["replied"] for x in ts) / n
            b = sum(x["bounced"] for x in ts) / n
            rows.append((r, n, b, name))
        for r, n, b, name in sorted(rows, reverse=True):
            # A rate over 3 trials is noise; say so rather than let it look
            # like a finding.
            note = "" if n >= 10 else "   (too few to trust)"
            print(f"  {name:26s} n={n:4d}  reply {r:5.1%}  bounce {b:5.1%}{note}")
        print()

    table("bucket", "who you contacted")
    table("style", "address format (guessed vs verified)")

    sized = [t for t in trials if t["length"] is not None]
    print(f"--- reply rate by length of your opening email "
          f"({len(sized)} openers) ---")
    for label, lo, hi in (("under 1200 chars", 0, 1200),
                          ("1200-2000", 1200, 2000),
                          ("over 2000", 2000, 10 ** 9)):
        ts = [t for t in sized if lo <= t["length"] < hi]
        if ts:
            r = sum(t["replied"] for t in ts) / len(ts)
            note = "" if len(ts) >= 10 else "   (too few to trust)"
            print(f"  {label:26s} n={len(ts):4d}  reply {r:5.1%}{note}")

    print(f"\n{'='*66}")
    print("Use this to choose the next contact, not to justify one you already"
          "\npicked — the sample per bucket is small and the rates move.")
