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
from company_match import _core as _core_key
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
def _evidence() -> list[tuple]:
    """(pattern, line) pairs matching a posting to a line of his experience.

    The lines name employers, so they live in resume_bank.json rather than
    here -- the repository is public and the bank is not.
    """
    import resume_bank
    rows = resume_bank.load().get("background", {}).get("evidence", [])
    return [(r["pattern"], r["text"]) for r in rows]


def _fallback() -> str:
    """Used when the posting matches none of the patterns."""
    import resume_bank
    return resume_bank.load().get("background", {}).get("evidence_fallback", "")


def _evidence_for(row: dict) -> str:
    blob = (f"{row.get('Position','')} "
            f"{row.get('Required Skills & Experience','')}").lower()
    for pattern, claim in _evidence():
        if re.search(pattern, blob):
            return claim
    return _fallback()


def build_hook(row: dict, contact: dict) -> str:
    """The middle paragraph, in whichever form the evidence actually supports.

    One template produced 44 drafts sharing three openings between them. The
    forms live in `hook_forms`; which one applies depends on what is known,
    and the choice is seeded on the recipient so regenerating is stable.
    """
    import hook_forms

    area = draft_writer.subject_topic(row.get("Position", "")).lower()
    company = draft_writer.display_company(row.get("Company", ""))
    duty = _first_duty(row.get("Required Skills & Experience", ""))
    title = (contact.get("title") or "").strip()
    seed = contact.get("email", "") or contact.get("name", "")

    if title and not title.startswith("("):
        curiosity = (contact.get("curiosity")
                     or "have become particularly interested in how "
                        "that work is judged from the inside.")
        return hook_forms.with_title(title, company, area, curiosity, seed)
    if duty:
        clause = duty[0].lower() + duty[1:]
        return hook_forms.with_posting(company, area, clause, seed)
    return hook_forms.team_only(company, area, seed)


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

    # Addresses and companies the researched set already covers. The two
    # generators run independently over the same sheet, and without this the
    # same person is drafted twice — once with a researched hook and once with
    # a generated one, both landing in the inbox of someone who did not ask.
    claimed: set = set()
    handled_companies: set = set()
    try:
        import json
        with open(draft_writer.CONTACTS_FILE) as fh:
            researched = json.load(fh)
        for company, contacts in researched.items():
            handled_companies.add(_core_key(company).replace(" ", ""))
            for c in contacts:
                claimed.add(c["email"].strip().lower())
        print(f"  ({len(claimed)} address(es) already covered by researched "
              f"contacts across {len(handled_companies)} company(ies))\n")
    except (OSError, ValueError):
        researched = {}
    total, companies, skipped = 0, 0, []
    for key, row in sorted(by_company.items(),
                           key=lambda kv: kv[1]["row_number"]):
        if _core_key(row.get("Company", "")).replace(" ", "") in handled_companies:
            skipped.append((row, "already covered by researched contacts"))
            continue
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
                # Three genuinely different things, previously collapsed
                # into one reassuring sentence. An address you once wrote to
                # and never heard back from is not "confirmed" — it is the
                # weakest evidence in the mailbox, and calling it confirmed is
                # how eli.nir@nomura.com came to be presented as a real
                # contact when the only trace of it was Ian's own unanswered
                # email from eighteen months earlier.
                "confidence": (
                    "they replied to you from this address, so it works"
                    if h.get("replied")
                    else "UNVERIFIED — you wrote to this address before and "
                         "never got a reply; it may be wrong, or they may "
                         "have left"
                    if h.get("delivered")
                    else "UNVERIFIED — only ever appeared in an unsent draft"),
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
