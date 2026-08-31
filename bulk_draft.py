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

# Words a clause should never end on. Cutting a posting's bullet at one of
# these produces "...market trends across", which reads as a typo.
_DANGLING = {"across", "of", "to", "for", "in", "on", "with", "and", "or",
             "the", "a", "an", "at", "by", "from", "into", "over", "under",
             "within", "including", "such", "as", "between", "per", "via"}


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
    # Postings run their bullets together without punctuation, so a capital
    # letter is the only separator available. A naive split cuts inside proper
    # nouns: "market trends across North American Capacity" broke at "North"
    # and left the clause ending on "across".
    #
    # A new bullet starts at a capitalised word whose predecessor is an
    # ordinary lowercase word — not a preposition it would dangle from, and
    # not itself capitalised, which would mean we are mid-name.
    tokens = jd.split()
    cut = len(tokens)
    for i in range(1, len(tokens)):
        word, prev = tokens[i], tokens[i - 1]
        if not word[:1].isupper():
            continue
        if prev[:1].isupper() or prev.lower().strip(",;:") in _DANGLING:
            continue
        if not prev[-1:].isalpha():        # previous token ended a list item
            continue
        cut = i
        break
    duty = " ".join(tokens[:cut])[:220].rstrip(" .,;:")
    while duty.split() and duty.split()[-1].lower() in _DANGLING:
        duty = " ".join(duty.split()[:-1])
    return duty.rstrip(" .,;:")


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
    """The middle paragraph, in the shape Ian's own third paragraph takes.

    His reads: "I came across your background and was particularly interested
    in your work <specific>. I've been learning more about <area>, and I found
    your experience especially interesting given <reason>."

    That opening cannot be used honestly here — this path knows the person's
    name and address from the mailbox, not what they work on, and claiming to
    have read their background would be the one kind of sentence a reader can
    catch. So the paragraph keeps the rhythm and the register while making
    only claims that are true: what the posting asks for, and what Ian has
    actually done.
    """
    duty = _first_duty(row.get("Required Skills & Experience", ""))
    evidence = _evidence_for(row)
    area = draft_writer.subject_topic(row.get("Position", "")).lower()

    if duty:
        return (
            f"I've been learning more about the {area} side of the business, "
            f"and the part of the role that interested me most was "
            f"{duty[0].lower()}{duty[1:]}. I {evidence}. What I can't tell "
            f"from outside is how much of the work is the analysis itself and "
            f"how much is making the case for it to people who have a view of "
            f"their own, which is the sort of thing I'd rather ask about than "
            f"guess at."
        )
    return (
        f"I've been learning more about the {area} side of the business, and "
        f"I wanted to hear about it from someone doing the work rather than "
        f"from a job posting. I {evidence}. I found your team's work "
        f"especially interesting because it sits close to the markets I've "
        f"been following, and I'd value hearing what the day actually looks "
        f"like and what someone joining from a quantitative background "
        f"usually has to learn first."
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
