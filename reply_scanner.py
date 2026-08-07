"""Detects conversations that actually started, and records them on the sheet.

Two columns move here, from three sources of evidence:

  * Status -> `interviewing`, when someone offers or schedules a call. Only
    rows still sitting at `applied` are touched: a row that already reached
    `interview-rejected`, `rejected` or `offer` has a later, better-known
    outcome and must never be dragged backwards.
  * Network -> Yes, when a real person at the company engaged with you at
    all. That is the question the Network column answers — did the outreach
    connect — and it stays Yes even if the role was later rejected.
    Recruiters count; an automated ATS address never does.

Evidence, weakest to strongest:
  1. Invitation wording in an inbound email ("availability for a phone screen").
  2. A human at the company's own domain replying to you at all.
  3. A calendar event with them — a record that the conversation happened,
     not an inference from wording.
"""

import datetime

import calendar_client
import corrections
import gmail_client
import interview_signals
import sheets
import status as status_mod
from company_match import company_mentioned, company_in_sender
from config import ME_RE
from link_finder import _disambiguate, _role_conflicts

# The Network column's vocabulary. It held TRUE/FALSE until the tracker was
# trimmed to A-J and the header became "Yes/No"; reads still accept the old
# spelling so a re-run does not re-flag rows written before the migration.
NETWORK_YES = "Yes"

# Gmail-side prefilter. Deliberately broader than the phrase list in
# interview_signals: this only decides which mail to download, and
# `invite_evidence` makes the real call.
INVITE_QUERY_PHRASES = [
    "phone screen",
    "screening call",
    "schedule an interview",
    "set up an interview",
    "invite you to interview",
    "your availability",
    "provide your availability",
    "zoom interview",
    "video interview",
    "would like to speak with you",
    "like to connect with you",
    "learn more about your background",
    "next round",
    "interview",
]


def _is_open(row: dict) -> bool:
    return status_mod.normalize(row.get("Status", "")) == status_mod.APPLIED


def _match_row(rows: list[dict], msg: dict) -> dict | None:
    """The single row this message is about, or None if unclear."""
    haystack = f"{msg['subject']} {msg.get('snippet', '')}".lower()
    matches = [
        r for r in rows
        if company_mentioned(r["Company"], haystack)
        or company_in_sender(r["Company"], msg.get("from", ""))
    ]
    if len(matches) > 1:
        resolved = _disambiguate(matches, msg)
        if resolved is None:
            return None
        matches = [resolved]
    if len(matches) == 1 and _role_conflicts(matches[0], msg):
        return None
    return matches[0] if len(matches) == 1 else None


def scan(days_back: int = 460, dry_run: bool = False,
         use_calendar: bool = True, max_results: int = 4000,
         notify: bool = False) -> list[dict]:
    rows = sheets.list_rows()

    # row_number -> (evidence for interviewing, evidence for networked)
    interview_ev: dict[int, str] = {}
    network_ev: dict[int, str] = {}

    # --- 1. Inbound mail: invitations, and any human reply at all ----------
    phrase_query = " OR ".join(f'"{p}"' for p in INVITE_QUERY_PHRASES)
    query = f"newer_than:{days_back}d -in:sent -in:chats ({phrase_query})"
    print(f"Searching Gmail: {query[:110]}...\n")
    messages = gmail_client.search_messages(query, include_body=True,
                                            max_results=max_results)
    print(f"Found {len(messages)} candidate message(s).\n")

    for msg in messages:
        if ME_RE.search(msg.get("from", "")):
            continue
        row = _match_row(rows, msg)
        if row is None:
            continue
        rn = row["row_number"]

        # A person writing from the company's own domain is a real connection,
        # whatever the message says.
        if (not interview_signals.is_automated(msg.get("from", ""), msg.get("subject", ""))
                and company_in_sender(row["Company"], msg.get("from", ""))):
            network_ev.setdefault(rn, f"reply from {msg['from'][:45]}")

        ev = interview_signals.invite_evidence(msg)
        if ev:
            interview_ev.setdefault(rn, f"email \"{ev}\"")
            # An invite proves a process is moving, but Network asks whether
            # you spoke to a *person*. Nasdaq was marked networked off an
            # invite from nasdaq@myworkday.com — nobody there ever wrote to
            # you. Only a human on the company's own domain counts.
            if (not interview_signals.is_automated(msg.get("from", ""), msg.get("subject", ""))
                    and company_in_sender(row["Company"], msg.get("from", ""))):
                network_ev.setdefault(rn, f"interview invite from {msg['from'][:35]}")

    # --- 1b. Your own thank-you notes --------------------------------------
    # These survive when the company's side does not: Gilmartin never sent a
    # rejection email, but your "thank you for your time earlier today" note
    # proves the interview happened.
    sent_query = (
        f'in:sent newer_than:{days_back}d ('
        'subject:"thank you" OR "thank you for your time" OR '
        '"pleasure speaking with you" OR "enjoyed our conversation" OR '
        '"opportunity to speak")'
    )
    sent_msgs = gmail_client.search_messages(sent_query, include_body=True,
                                             max_results=800)
    print(f"Found {len(sent_msgs)} sent thank-you candidate(s).\n")
    for msg in sent_msgs:
        # Two different questions: did you interview (Status), and did the
        # outreach connect at all (Network). A coffee chat answers only the
        # second.
        int_ev = interview_signals.self_interview_evidence(msg)
        net_ev = interview_signals.self_networking_evidence(msg)
        if not int_ev and not net_ev:
            continue
        # Your note names the company in the subject or the quoted reply.
        probe = dict(msg)
        probe["snippet"] = (msg.get("body") or "")[:1500]
        row = _match_row(rows, probe)
        if row is None:
            continue
        if int_ev:
            interview_ev.setdefault(row["row_number"], int_ev)
        if net_ev:
            network_ev.setdefault(row["row_number"], net_ev)

    # --- 2. Calendar: proof the conversation actually happened -------------
    events = []
    if use_calendar:
        try:
            events = calendar_client.list_events(days_back=days_back)
            print(f"Read {len(events)} calendar event(s).\n")
        except Exception as exc:
            print(f"Calendar unavailable ({exc}).\n"
                  "  Re-authorize with: rm token.json && ./venv/bin/python cli.py list\n")

    for row in rows:
        meetings = calendar_client.company_meetings(row["Company"], events)
        if not meetings:
            continue
        _, ev = meetings[0]
        rn = row["row_number"]
        # Calendar outranks wording, so it replaces any weaker evidence.
        interview_ev[rn] = ev
        network_ev[rn] = ev

    # --- 3. Apply -----------------------------------------------------------
    by_number = {r["row_number"]: r for r in rows}
    status_updates, upgrades, network_updates = [], [], []

    for rn, ev in sorted(interview_ev.items()):
        row = by_number[rn]
        # A row you have ruled on is never re-derived. This is what stops the
        # same coffee-chat note re-flagging bp and Liberty Mutual as
        # interviews after you have already said they were not.
        if corrections.is_locked(row, "Status"):
            continue
        current = status_mod.normalize(row.get("Status", ""))
        if current == status_mod.APPLIED:
            status_updates.append((row, ev))
        elif current == status_mod.REJECTED:
            # Already known to be over, but not that you got to a conversation
            # first. That is the distinction the yellow rows record.
            upgrades.append((row, ev))

    for rn, ev in sorted(network_ev.items()):
        row = by_number[rn]
        if corrections.is_locked(row, "Network"):
            continue
        # Accepts the old TRUE as well as the current Yes, so a re-run after
        # the column was migrated does not re-flag every row.
        if (row.get("Network") or "").strip().upper() not in ("YES", "TRUE"):
            network_updates.append((row, ev))

    print(f"{'Would set' if dry_run else 'Set'} Status=interviewing on "
          f"{len(status_updates)} row(s):")
    for row, ev in status_updates:
        print(f"  row {row['row_number']}: {row['Position'][:42]} @ {row['Company']}")
        print(f"        evidence: {ev}")

    print(f"\n{'Would upgrade' if dry_run else 'Upgraded'} {len(upgrades)} row(s) "
          f"rejected -> interview-rejected:")
    for row, ev in upgrades:
        print(f"  row {row['row_number']}: {row['Position'][:42]} @ {row['Company']}")
        print(f"        evidence: {ev}")

    print(f"\n{'Would set' if dry_run else 'Set'} Network=Yes on "
          f"{len(network_updates)} row(s):")
    for row, ev in network_updates:
        print(f"  row {row['row_number']}: {row['Position'][:42]} @ {row['Company']}")
        print(f"        evidence: {ev}")

    # Anything that moved is something a person is waiting on you for.
    reachouts = [
        {
            "key": f"{row['row_number']}:{kind}:{ev[:40]}",
            "kind": kind,
            "company": row["Company"],
            "role": row["Position"],
            "who": "",
            "detail": ev,
            "row": row["row_number"],
        }
        for kind, group in (("interview invite", status_updates),
                            ("reply / connection", network_updates))
        for row, ev in group
    ]

    if dry_run:
        print("\n(dry-run — nothing written)")
        if notify:
            import notify as notify_mod
            notify_mod.send_reachout_reminder(reachouts, dry_run=True)
        return reachouts

    today = datetime.date.today().isoformat()
    for row, ev, new_status in (
        [(r, e, status_mod.INTERVIEWING) for r, e in status_updates]
        + [(r, e, status_mod.INTERVIEW_REJECTED) for r, e in upgrades]
    ):
        note = f"Interview detected {today} [{ev}]"
        notes = f"{row.get('Notes', '')}\n{note}".strip()
        sheets.update_row(row["row_number"], {"Status": new_status, "Notes": notes})
    for row, ev in network_updates:
        sheets.update_row(row["row_number"], {"Network": NETWORK_YES})

    print(f"\nWrote {len(status_updates) + len(upgrades)} status change(s) and "
          f"{len(network_updates)} Network flag(s).")

    if notify:
        import notify as notify_mod
        notify_mod.send_reachout_reminder(reachouts)
    return reachouts
