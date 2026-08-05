"""Finds warm leads that went cold — the people worth reaching out to again.

This is the opposite of follow_up.py. That one finds outreach nobody ever
answered. This one finds threads where a real person at the company *did*
answer, and then the conversation simply stopped without a rejection.

Those are your best remaining leads and the easiest to lose: Mirae Asset
replied twice in June 2026 (Simon Lee, then Chris Hempstead), went quiet, and
never even made it onto the sheet.

Ranked by how warm the lead was and how long it has been silent. Read-only —
it reports, it never writes or emails anyone.
"""

import datetime
import re
from email.utils import parsedate_to_datetime

import gmail_client
import interview_signals
import rejection_scanner
import sheets
from company_match import _domain_label, _is_vendor_domain, company_mentioned
from calendar_client import _GENERIC_DOMAINS
from config import ME_RE


def _date(msg: dict):
    try:
        return parsedate_to_datetime(msg["date"]).date()
    except Exception:
        return None


# Mail-transport infrastructure. A message from one of these is a bounce
# report, never a person.
_BOUNCE_DOMAIN_RE = re.compile(
    r"(pphosted|proofpoint|reinject|onmicrosoft|mailgw|mimecast|barracuda|"
    r"messagelabs|mailcontrol|ppops|sendercompany|sendgrid|amazonses)", re.I)


def _company_domain(msg: dict) -> str:
    """The company's own domain from a sender, or '' for vendors/webmail."""
    frm = msg.get("from", "")
    if "@" not in frm:
        return ""
    # A bounce is the opposite of a warm lead: it means your email never
    # arrived. Counting postmaster@ and MAILER-DAEMON@ as replies invented
    # "leads" at companies that never received the outreach at all.
    if interview_signals.is_automated(frm, msg.get("subject", "")) or _BOUNCE_DOMAIN_RE.search(frm):
        return ""
    domain = frm.rsplit("@", 1)[1].lower().strip(" >")
    if _is_vendor_domain(domain) or domain in _GENERIC_DOMAINS:
        return ""
    label = _domain_label(frm)
    return label if len(label) >= 3 else ""


def scan(days_back: int = 520, min_silent_days: int = 21,
         limit: int = 600, dry_run: bool = True) -> list[dict]:
    import cli

    query = f"in:sent newer_than:{days_back}d {cli.NETWORKING_KEYWORDS}"
    print(f"Searching Gmail: {query}\n", flush=True)
    sent = gmail_client.search_messages(query, max_results=limit)
    print(f"{len(sent)} outreach message(s); grouping into threads...\n", flush=True)

    today = datetime.date.today()
    rows = sheets.list_rows()

    leads: dict[str, dict] = {}
    seen_threads = set()

    for msg in sent:
        tid = msg.get("threadId") or msg["id"]
        if tid in seen_threads:
            continue
        seen_threads.add(tid)
        if len(seen_threads) % 25 == 0:
            print(f"  ...{len(seen_threads)} threads examined", flush=True)

        # Headers only. Bodies are needed just for the rejection and invite
        # checks, and most threads are filtered out before that on dates
        # alone — fetching every body up front made this take ten minutes.
        try:
            thread = gmail_client.get_thread(tid, include_body=False)
        except Exception:
            continue

        inbound = [m for m in thread if not ME_RE.search(m.get("from", ""))
                   and _company_domain(m)]
        if not inbound:
            continue  # never answered — that is follow_up.py's job

        last_in = max(inbound, key=lambda m: _date(m) or datetime.date.min)
        last_date = _date(last_in)
        if not last_date:
            continue

        last_any = max((d for d in (_date(m) for m in thread) if d),
                       default=last_date)
        silent = (today - last_any).days
        if silent < min_silent_days:
            continue

        # Now it matters what the messages actually said.
        try:
            thread = gmail_client.get_thread(tid, include_body=True)
        except Exception:
            continue
        inbound = [m for m in thread if not ME_RE.search(m.get("from", ""))
                   and _company_domain(m)]
        if not inbound:
            continue

        # A rejection means the conversation ended on purpose.
        if any(rejection_scanner._is_real_rejection(m) for m in inbound
               if m.get("body")):
            continue

        last_in = max(inbound, key=lambda m: _date(m) or datetime.date.min)
        domain = _company_domain(last_in)
        # An interview invite in the thread makes this much warmer than a
        # plain "thanks for reaching out".
        warm = any(interview_signals.invite_evidence(m) for m in inbound
                   if m.get("body"))

        existing = leads.get(domain)
        if existing and existing["silent"] <= silent:
            existing["repliers"].add(last_in["from"])
            continue

        leads[domain] = {
            "domain": domain,
            "company": domain,
            "silent": silent,
            "last_contact": last_any,
            "subject": last_in.get("subject", ""),
            "repliers": {last_in["from"]},
            "warm": warm,
        }

    # Attach the sheet row, if there is one. Domain first: matching the
    # company name against the subject alone tied Mirae Asset's "ETF and
    # Global Portfolio Trading" thread to a row called "Global".
    from company_match import _core, normalize
    for lead in leads.values():
        domain = lead["domain"]
        match = next(
            (r for r in rows
             if domain and (normalize(r["Company"]).replace(" ", "") == domain
                            or _core(r["Company"]).replace(" ", "") == domain
                            or domain.startswith(
                                _core(r["Company"]).replace(" ", "")[:8]))),
            None,
        )
        if match is None:
            # Subject fallback, but only when exactly one row can claim it.
            subject = lead["subject"].lower()
            candidates = [r for r in rows
                          if company_mentioned(r["Company"], subject)]
            match = candidates[0] if len(candidates) == 1 else None
        lead["row"] = match["row_number"] if match else None
        lead["status"] = match.get("Status", "") if match else ""
        if match:
            lead["company"] = match["Company"]

    ranked = sorted(leads.values(),
                    key=lambda l: (not l["warm"], -l["silent"]))

    print(f"{len(ranked)} warm lead(s) that went quiet "
          f"({min_silent_days}+ days, no rejection):\n")
    for lead in ranked:
        tag = "WARM" if lead["warm"] else "    "
        where = f"row {lead['row']}" if lead["row"] else "NOT IN SHEET"
        print(f"  {tag} | {lead['silent']:4d}d silent | {lead['company'][:26]:26s} "
              f"| {where:12s} | {lead['status'][:18]}")
        for who in sorted(lead["repliers"])[:2]:
            print(f"         replied: {who[:60]}")

    if ranked:
        print("\nWARM = someone offered a call in that thread. "
              "To draft a follow-up:")
        print("  python cli.py auto-draft-outreach --row <N> --to <email> --follow-up")
    return ranked
