"""Finds outreach that has gone quiet and drafts one second touch for each.

"These 12 hit 7 days with no reply, send touch #2" -- the thing done by feel
until now, made into a function. The default wait is the measured one
(followup_model.timing), not a round number.

Three rules, all decided from the Gmail thread rather than a state file, so
nothing can drift out of sync with what was actually sent:

  reply?      any message in the thread from someone else -> not silent, skip.
  once only   two or more sends of his already in the thread -> he has
              followed up, skip. There is no touch #3, ever.
  too soon    the first send is younger than the wait -> not yet.
  pending     a DRAFT of his already in the thread -> handled, skip. This is
              also what makes re-running safe: nothing is drafted twice.

Drafts only. Nothing is sent. Each draft is a reply inside the original
thread, so it lands under the first email in both inboxes. The words come
from the same subscription-backed CLI path auto_draft uses, conditioned on
his own verified follow-ups.
"""

import datetime
import re
import time
from email.utils import parsedate_to_datetime

import followup_model
import gmail_client

from config import ME_RE, NETWORKING_KEYWORDS

SYSTEM = """You write ONE short follow-up email for Ian Liu to a person who has not
replied to his first email. The first email is shown; do not repeat it.

Rules
- This is the only follow-up he will send. Do not promise another.
- Do not re-introduce Ian or restate his background. The first email did that.
- Do not thank them for a reply. There was none.
- Plain, specific, unhurried. No exclamation marks. No "just checking in".
- Keep the original ask (a short conversation) and, if the first email
  mentioned something specific about them, refer back to it in one clause.
- Sign off exactly: "Sincerely,\\nIan Liu" -- that is how 8 of his 11
  real follow-ups end, unlike his first emails.

{FOLLOWUP_PROFILE}

Return only the email body, starting with the greeting. No subject line, no
commentary, no quoting of the first email."""


def _first_name(to: str) -> str:
    name = re.sub(r"<.*?>", "", to or "").strip().strip('"')
    if not name:
        name = (to or "").split("@")[0]
    # "Last, First" is common from corporate address books.
    if "," in name:
        name = name.split(",", 1)[1].strip()
    return name.split()[0] if name.split() else "there"


def _sent_date(msg: dict):
    try:
        return parsedate_to_datetime(msg["date"]).date()
    except Exception:
        return None


def candidates(min_age_days: int = 7, max_age_days: int = 30,
               limit: int = 400) -> list[dict]:
    """Threads owed a second touch, oldest-silence first.

    Bounded on both sides. Below min_age it is too soon. Above max_age the
    moment has passed: his own follow-ups sit in a 4-14 day window, and a
    nudge about a note from months back reads as odd. Older silence is
    reoutreach.py's job, which re-warms rather than nudges. The ceiling also
    keeps the Gmail search narrow, which is what keeps the thread walk under
    the rate limit.
    """
    query = f"in:sent newer_than:{max_age_days + 7}d {NETWORKING_KEYWORDS}"
    msgs = gmail_client.search_messages(query, max_results=limit)
    if not msgs:
        raise RuntimeError(
            "Gmail returned no sent outreach. That is usually rate limiting "
            "rather than an empty mailbox -- wait a minute and re-run.")

    today = datetime.date.today()
    out, seen = [], set()
    for msg in msgs:
        tid = msg.get("threadId") or msg["id"]
        if tid in seen:
            continue
        seen.add(tid)

        # One fetch per thread; a short pause every batch keeps a long walk
        # from tripping the per-user limit and silently dropping threads.
        if len(seen) % 25 == 0:
            time.sleep(2)

        thread = gmail_client.get_thread(tid, include_body=True)
        if not thread:
            continue
        mine = [m for m in thread if ME_RE.search(m.get("from", ""))]
        theirs = [m for m in thread if not ME_RE.search(m.get("from", ""))]
        # A pending draft is in the thread too, marked DRAFT. It must not
        # count as a send for the once-only rule, but it does mean this
        # contact is already handled.
        sent = [m for m in mine if "DRAFT" not in m.get("labels", [])]
        has_draft = len(sent) < len(mine)

        if theirs:              # they replied
            continue
        if len(sent) >= 2:      # already followed up once -- never a third
            continue
        if not sent:
            continue

        original = sent[0]
        sent_on = _sent_date(original)
        if sent_on is None:
            continue
        age = (today - sent_on).days
        if age < min_age_days or age > max_age_days:
            continue

        out.append({
            "thread_id": tid,
            "message_id": original.get("message_id", ""),
            "to": original.get("to", ""),
            "subject": original.get("subject", ""),
            "sent": sent_on,
            "age": age,
            "has_draft": has_draft,
            "original_body": followup_model._strip_quote(
                original.get("body", "")),
        })

    out.sort(key=lambda c: -c["age"])
    return out


def draft_body(cand: dict) -> str:
    """One follow-up in his voice, via the subscription-backed CLI."""
    from auto_draft import _via_cli

    profile = followup_model.prompt_block(area=cand["subject"])
    system = SYSTEM.replace("{FOLLOWUP_PROFILE}", profile)
    prompt = (
        f"Recipient first name: {_first_name(cand['to'])}\n"
        f"Days since the first email: {cand['age']}\n\n"
        f"THE FIRST EMAIL HE SENT\n"
        f"Subject: {cand['subject']}\n\n{cand['original_body']}\n\n"
        f"Write the follow-up."
    )
    text = _via_cli(system, prompt).strip()
    # Belt and braces: if the model wraps the body in a code fence or
    # prefixes a label, keep only what starts at the greeting.
    text = re.sub(r"^```.*?\n|```$", "", text, flags=re.S).strip()
    match = re.search(r"^(Dear|Hello|Hi)\b.*", text, flags=re.S | re.M)
    return match.group(0).strip() if match else text


def run(min_age_days: int | None = None, max_age_days: int = 30,
        limit: int = 400, max_drafts: int = 12, dry_run: bool = True) -> None:
    if min_age_days is None:
        learned = followup_model.timing()
        min_age_days = learned.get("median_days", 7) if learned else 7

    due = candidates(min_age_days=min_age_days, max_age_days=max_age_days,
                     limit=limit)
    fresh = [c for c in due if not c["has_draft"]]
    skipped = len(due) - len(fresh)

    print(f"{len(due)} contact(s) hit {min_age_days} days with no reply and "
          f"no follow-up yet (within the last {max_age_days} days)"
          + (f"; {skipped} already have a draft waiting" if skipped else "")
          + ".\n")
    for c in fresh:
        print(f"  {c['age']:3d}d | {c['sent']} | {c['to'][:40]:40s} | "
              f"{c['subject'][:48]}")

    if not fresh:
        return
    if dry_run:
        print(f"\nDry run. To draft touch #2 for up to {max_drafts} of these:")
        print("  python cli.py schedule-followups --draft")
        return

    print(f"\nDrafting up to {max_drafts}...\n")
    for c in fresh[:max_drafts]:
        try:
            body = draft_body(c)
        except Exception as exc:
            print(f"  ! {c['to'][:40]}: {type(exc).__name__}: {str(exc)[:100]}")
            continue
        gmail_client.create_draft(
            to=c["to"],
            subject=f"Re: {c['subject']}",
            body_text=body,
            thread_id=c["thread_id"],
            in_reply_to=c["message_id"] or None,
        )
        print(f"  drafted -> {c['to'][:44]}  ({len(body.split())} words)")

    print("\nDrafts are in Gmail under the original threads. Nothing was sent.")


if __name__ == "__main__":
    import sys
    run(dry_run="--draft" not in sys.argv)
