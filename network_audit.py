"""Re-checks the Network column against the "did you actually talk" rule.

The column was originally filled by a looser test — any human on the company's
own domain writing to you at all. That swept in recruiters' rejection letters,
which are written by real people and are not connections. This re-derives the
column from scratch and clears the rows that never met the stricter bar.

Evidence accepted here is the same as `reply_scanner` now grants:
  * someone at the company replied inside a thread you had written into first;
  * you wrote them a note that only makes sense after a conversation
    ("pleasure speaking with you earlier today").

Calendar evidence is deliberately NOT re-derived. If the calendar is not
authorised, every coffee chat that lives only there would look like no
evidence at all, and the audit would clear rows that are correct. Rows whose
Network flag came from a calendar event are protected by locking them instead.
"""

import interview_signals
import corrections
import gmail_client
import reply_scanner
import sheets
import status as status_mod
from company_match import company_in_sender, company_mentioned
from config import ME_RE

NETWORK_NO = "No"


def _networked_companies(days_back: int, verbose: bool = True) -> dict:
    """company-name -> evidence, for every company you genuinely spoke with."""
    mine = reply_scanner._my_thread_times(days_back)
    if verbose:
        print(f"You wrote into {len(mine)} thread(s).")

    rows = sheets.list_rows()
    flagged = [r for r in rows
               if (r.get("Network") or "").strip().upper() in ("YES", "TRUE")]
    companies = sorted({(r.get("Company") or "").strip()
                        for r in flagged if (r.get("Company") or "").strip()})
    if verbose:
        print(f"{len(flagged)} row(s) flagged Network=Yes across "
              f"{len(companies)} company(ies).")

    inbound = gmail_client.search_messages(
        f"newer_than:{days_back}d -in:sent -in:chats",
        include_body=False, max_results=8000)
    if verbose:
        print(f"Scanning {len(inbound)} inbound message(s).")

    evidence: dict[str, str] = {}
    for msg in inbound:
        sender = msg.get("from", "")
        if ME_RE.search(sender):
            continue
        if interview_signals.is_automated(sender, msg.get("subject", "")):
            continue
        if not reply_scanner._answered_me(msg, mine):
            continue
        for company in companies:
            if company in evidence:
                continue
            if company_in_sender(company, sender):
                evidence[company] = f"replied to you — {sender[:60]}"

    # Your own post-conversation notes, which outlive the other side's mail.
    notes = gmail_client.search_messages(
        f'in:sent newer_than:{days_back}d '
        '("thank you for your time" OR "pleasure speaking with you" OR '
        '"enjoyed our conversation" OR "great speaking with you" OR '
        '"thanks for taking the time" OR "opportunity to speak")',
        include_body=True, max_results=1000)
    if verbose:
        print(f"Scanning {len(notes)} note(s) you sent.")
    for msg in notes:
        ev = interview_signals.self_networking_evidence(msg)
        if not ev:
            continue
        hay = (f"{msg.get('subject', '')} {msg.get('to', '')} "
               f"{(msg.get('body') or '')[:1500]}").lower()
        for company in companies:
            if company in evidence:
                continue
            if company_mentioned(company, hay) or company_in_sender(
                    company, msg.get("to", "")):
                evidence[company] = ev
    return evidence


def run(days_back: int = 460, dry_run: bool = True) -> None:
    evidence = _networked_companies(days_back)
    # Reaching an interview means you spoke to somebody, whatever survives in
    # Gmail. These are never cleared automatically — an async video screen is
    # the one case where it would be wrong, and that is your call, not a
    # guess this module should make.
    SPOKE = {status_mod.INTERVIEWING, status_mod.INTERVIEW_REJECTED,
             status_mod.OFFER}

    rows = sheets.list_rows()
    keep, clear, ask = [], [], []
    for row in rows:
        if (row.get("Network") or "").strip().upper() not in ("YES", "TRUE"):
            continue
        company = (row.get("Company") or "").strip()
        if corrections.is_locked(row, "Network"):
            keep.append((row, "locked by you"))
        elif company in evidence:
            keep.append((row, evidence[company]))
        elif status_mod.normalize(row.get("Status", "")) in SPOKE:
            ask.append(row)
        else:
            clear.append(row)

    print(f"\n{len(keep)} row(s) keep Network=Yes:")
    for row, ev in keep:
        print(f"  row {row['row_number']:3d} | {row['Company'][:26]:26s} | {ev[:58]}")

    print(f"\n{len(clear)} row(s) {'would be' if dry_run else ''} cleared "
          f"— no two-way exchange found:")
    for row in clear:
        print(f"  row {row['row_number']:3d} | {row['Company'][:26]:26s} | "
              f"{row['Status'][:18]}")

    print(f"\n{len(ask)} row(s) kept but unproven — you reached an interview, "
          f"so\nyou spoke to someone, but no exchange survives in Gmail:")
    for row in ask:
        print(f"  row {row['row_number']:3d} | {row['Company'][:26]:26s} | "
              f"{row['Status'][:18]}")

    if dry_run:
        print("\n(dry-run — nothing written)")
        return
    for row in clear:
        sheets.update_row(row["row_number"], {"Network": NETWORK_NO})
    print(f"\nCleared {len(clear)} row(s) to No.")
