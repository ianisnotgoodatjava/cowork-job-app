"""Drafts outreach for every recent application that has a reachable contact.

`draft_writer` finishes one email when somebody has already done the research.
`auto_draft` does the research through the Claude API. This covers the case in
between, which turns out to be most of them: the person is already in your
mailbox, so no research is needed at all — only matching them to the right
application and writing something specific enough to be worth reading.

Two rules that matter more than the wording:

  * One email per person, ever. Four applications to Nomura must not become
    four emails to the same analyst. Contacts are claimed globally, and a
    company is drafted once against its strongest open application.
  * The opening names what the posting actually asks for and answers it with
    the closest thing you have done. Where the sheet has no description there
    is nothing specific to say, so the row is skipped rather than filled with
    a paragraph that could have been sent to anyone.

Nothing is sent. Output is files, and optionally unsent Gmail drafts.
"""

import datetime
import re

import contact_finder
import draft_writer
import sheets
import status as status_mod
from date_utils import parse_date

MAX_PER_COMPANY = 3


def _first_duty(jd: str) -> str:
    """The first responsibility from a posting, as a clause you can quote."""
    jd = " ".join((jd or "").split())
    if not jd:
        return ""
    try:
        import enrich
        if enrich._is_benefits(jd):
            return ""
    except Exception:
        pass
    # Postings run bullets together without punctuation, so a capital letter
    # after a lowercase one is the real separator far more often than a period.
    duty = re.split(r"(?<=[a-z,)])\s+(?=[A-Z])", jd)[0]
    return duty[:200].rstrip(" .,;:")


# Ian's experience, keyed by what a posting is asking for. Ordered: the first
# match wins, so the most specific claims sit at the top.
_EVIDENCE = [
    (r"\b(model|modell?ing|quantitative|statistic|forecast|econometric|"
     r"regression|optimi[sz])\b",
     "researched FX volatility with ARMA-GARCH models at REDACTED and "
     "backtested G10 rates momentum and currency carry strategies, optimising "
     "the factor set rather than accepting the first result that looked good"),
    (r"\b(fixed income|credit|bond|securit|abs|cmbs|rmbs|clo|structured)\b",
     "rotated across the Rates Trading and Emerging Markets FX desks at Bank "
     "REDACTED and wrote the daily commentary on China's and Japan's currency "
     "and fixed income markets"),
    (r"\b(energy|power|renewable|commodit|solar|infrastructure|utilit)\b",
     "evaluated infrastructure and renewable energy companies under project "
     "finance assumptions at REDACTED, and researched the "
     "performance and sustainability metrics of impact and ESG funds"),
    (r"\b(portfolio|allocation|fund|asset management|investment)\b",
     "built and optimised backtested portfolios for G10 rates momentum and "
     "currency carry, and processed roughly 150,000 fund manager documents "
     "into comparable data at REDACTED"),
    (r"\b(trading|trader|sales|desk|execution|market making|liquidity)\b",
     "spent a summer on the Rates, Emerging Markets FX and FX Quant desks at "
     "REDACTED, writing the morning commentary the desk traded off"),
    (r"\b(risk|underwrit|exposure|pricing|insurance)\b",
     "review property submissions and shadow underwriters pricing risk at "
     "REDACTED, which is diligence with money behind it"),
    (r"\b(data|python|sql|automation|pipeline|analytics|machine learning)\b",
     "designed supervised machine-learning pipelines in Python and JavaScript "
     "over roughly 150,000 documents at REDACTED"),
]

_FALLBACK = ("rotated across the Rates, Emerging Markets FX and FX Quant desks "
             "at REDACTED and have done quantitative work in Python and R "
             "since")


def _evidence_for(row: dict) -> str:
    blob = (f"{row.get('Position','')} "
            f"{row.get('Required Skills & Experience','')}").lower()
    for pattern, claim in _EVIDENCE:
        if re.search(pattern, blob):
            return claim
    return _FALLBACK


def build_hook(row: dict, contact: dict) -> str:
    duty = _first_duty(row.get("Required Skills & Experience", ""))
    evidence = _evidence_for(row)
    role = (row.get("Position") or "the role").strip()

    if duty:
        return (
            f"The posting describes {duty[0].lower()}{duty[1:]} — which is the "
            f"part I wanted to ask someone doing the work about, rather than a "
            f"recruiter. I {evidence}. What I cannot tell from outside is how "
            f"much of the job is the analysis itself and how much is arguing "
            f"the assumptions with people who have a view of their own. How "
            f"does that split look on your team?"
        )
    # The opening paragraph has already said he applied, so repeating it here
    # made the email state the same thing twice in consecutive sentences.
    return (
        f"Rather than let that sit in a queue, I wanted to hear about the work "
        f"from someone doing it. I {evidence}. What a posting never tells you "
        f"is which part of the job actually takes the time — the analysis "
        f"itself, or making the case for it to people who have their own view. "
        f"I would rather ask than guess."
    )


def _subject(row: dict) -> str:
    """Your own convention, not a new one invented for this tool."""
    return draft_writer.subject_for(row)


def run(last: int = 50, per_company: int = MAX_PER_COMPANY,
        create_drafts: bool = False, days_back: int = 700,
        require_description: bool = False) -> None:
    print("Reading your mailbox for people you already know...", flush=True)
    people = contact_finder.harvest(days_back=days_back)
    print(f"  {len(people)} human address(es)\n", flush=True)

    rows = sorted(sheets.list_rows(),
                  key=lambda r: parse_date(r.get("Applied Draes", ""))
                  or datetime.date.min)[-last:]
    open_rows = [r for r in rows if status_mod.is_open(r.get("Status", ""))]

    # One company, one conversation. Drafted against its most recent open
    # application, since that is the one worth mentioning.
    by_company: dict[str, dict] = {}
    for row in open_rows:
        from company_match import _core
        key = _core(row.get("Company", "")).replace(" ", "")
        if not key:
            continue
        best = by_company.get(key)
        if best is None or (parse_date(row.get("Applied Draes", ""))
                            or datetime.date.min) >= (
                                parse_date(best.get("Applied Draes", ""))
                                or datetime.date.min):
            by_company[key] = row

    existing = draft_writer._existing_drafts() if create_drafts else None
    claimed: set = set()
    total, companies, skipped = 0, 0, []
    for key, row in sorted(by_company.items(),
                           key=lambda kv: kv[1]["row_number"]):
        hits = [h for h in contact_finder.for_company(row["Company"], people)
                if h["email"].lower() not in claimed
                and contact_finder.has_real_name(h)]
        if not hits:
            skipped.append((row, "no named contact in your mail"))
            continue
        if require_description and not (
                row.get("Required Skills & Experience") or "").strip():
            skipped.append((row, "no job description on the row"))
            continue

        contacts = []
        for h in hits[:per_company]:
            claimed.add(h["email"].lower())
            contacts.append({
                "name": contact_finder.best_name(h),
                "title": "(title not known — from your mail history)",
                "email": h["email"],
                "source": ("they replied to you before" if h["replied"]
                           else "you have written to them before" if h["wrote_to"]
                           else "appeared in a thread with this company"),
                "confidence": ("address confirmed by delivered mail"
                               if h.get("delivered")
                               else "from an unsent draft — the address has "
                                    "never actually been delivered to, so "
                                    "treat it as unverified"),
                "subject": _subject(row),
                "hook": build_hook(row, h),
            })

        draft_writer.write(row, contacts, create_drafts=create_drafts,
                           existing=existing)
        companies += 1
        total += len(contacts)
        print(f"  row {row['row_number']:3d} | {row['Company'][:24]:24s} | "
              f"{len(contacts)} draft(s): "
              + ", ".join(c["name"] for c in contacts))

    print(f"\n{total} draft(s) across {companies} company(ies).")
    if skipped:
        print(f"\n{len(skipped)} skipped:")
        for row, why in skipped:
            print(f"  row {row['row_number']:3d} | {row['Company'][:24]:24s} | {why}")
    print(f"\nFiles in {draft_writer.OUT_DIR}/"
          + ("; also created as unsent Gmail drafts." if create_drafts else "."))
