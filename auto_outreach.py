"""Drafts outreach to a team the moment an application lands on the sheet.

The gap this closes has been the same one all along: an application gets
logged, and then nothing happens until someone sits down and researches a
person by hand. Every piece needed to do it automatically already existed and
none of them were connected --

  company_formats.domain_for   which domain the employer reads mail at
  people_search.find           who works there, ranked by desk and seniority
  company_formats.address_for  their address, from a convention with evidence
  recent_contacts              minus anyone written to inside the year
  draft_writer                 the Gmail draft itself

so this is wiring, not new machinery, and it runs straight after
app_scanner.scan in the pipeline.

Three contacts per application, because one is a single point of failure --
people leave, filter, and ignore mail -- and ten is what turned Drafts into
twelve Nomura emails nobody would send.

What it will not do is invent. No contact, no domain, no address convention
means no draft and a line saying which of the three was missing. A draft to a
guessed address costs a bounce, and bounces are charged against the domain for
every later email sent there.
"""

import os

import company_formats
import gmail_client
import people_search
import recent_contacts
import status as status_mod

CONTACTS_PER_APPLICATION = 3
RESUME = os.path.expanduser("~/Ian_Liu_Resume.pdf")


def _body(company: str, role: str, contact: dict, bullets: list[str]) -> str:
    """The email, in the five-paragraph shape the sent corpus measures.

    Structure is measured rather than invented -- see style_profile: 88% of
    real subjects use one template, the median body is 152 words, and 97% ask
    for 15-20 minutes. What cannot be automated is the specific reason for
    writing to this person, so the paragraph that would carry it says only
    what is true: the desk they are on, and the work actually done.
    """
    title = (contact.get("title") or "").strip()
    # "since you senior Vice President Internal Audit there" -- a job title is
    # a noun, not a verb phrase, and splicing it in as one produced exactly the
    # sentence a reader stops at.
    # "an" goes by sound, not spelling, and the two cases differ: the word
    # "Senior" takes "a", the acronym "SVP" takes "an" because it is read
    # letter by letter. Checking the vowel rule alone produced "an Senior".
    first = title.split()[0] if title else ""
    if first.isupper() and len(first) > 1:
        article = "an" if first[0] in "AEIOUFHLMNRSX" else "a"
    else:
        article = "an" if first[:1].upper() in "AEIOU" else "a"
    who = f"{article} {title} there" if title else "someone working there"

    # "Dear Gupta," reads as a stranger addressing a file. Without knowing how
    # someone is titled, the first name is the safe form and the one the sent
    # corpus already uses ("Dear Lanyon,").
    parts = [w for w in contact["name"].split() if len(w) > 1]
    greeting = parts[0] if parts else contact["name"]

    lead = (bullets[0] if bullets else "").strip()
    if lead and not lead.endswith((".", "!", "?")):
        lead += "."
    middle = "My background is quantitative, but the work I have found most useful has been applied."
    if lead:
        middle += f" {lead}"
    middle += (" What I can't tell from outside is how much of the role is the "
               "analysis itself and how much is making the case for it to "
               "people who have a view of their own, which is the sort of "
               "thing I'd rather ask about than guess at.")

    return "\n\n".join([
        f"Dear {greeting},",
        "I hope you're doing well. My name is Ian Liu, and I recently "
        "graduated from NYU with a degree in Mathematics and Economics. I'm "
        f"reaching out because I applied to the {role} role at {company}, and "
        f"as {who} you seemed like a good person to ask about the work.",
        middle,
        "If you'd be open to a brief 15-20 minute conversation by phone, I'd "
        "be grateful for the chance to hear how you approach it. I've "
        "attached my resume for context.",
        "Thank you for your time, and I hope to speak with you soon.",
        "Best regards,\nIan Liu\nicl2016@nyu.edu | REDACTED",
    ])


def _bullets(row: dict) -> list[str]:
    try:
        import prep_package
        bank = prep_package.load_bank()
        parsed = {"requirements": [row.get("Required Skills & Experience", "")]}
        category = prep_package.detect_category(row.get("Position", ""), parsed)
        return [b["text"] for _, b in
                prep_package.rank_bullets(bank, parsed, category)[:1]]
    except Exception:
        return []


def for_row(row: dict, want: int = CONTACTS_PER_APPLICATION,
            dry_run: bool = False) -> dict:
    """Finds contacts for one application and drafts to them."""
    company = (row.get("Company") or "").strip()
    role = (row.get("Position") or "").strip()
    result = {"company": company, "role": role, "drafted": [], "why": ""}

    domain, note = company_formats.domain_for(company)
    if not domain:
        result["why"] = f"no mail domain — {note}"
        return result

    found = people_search.find(company, role=role, domain=domain)
    candidates = found["candidates"][:want]
    if not candidates:
        blocked = len(found["dropped"])
        result["why"] = (f"no reachable contact at {domain}"
                         + (f" ({blocked} found but not writable)" if blocked else ""))
        return result

    # The subject shape 88% of the sent corpus uses. See style_profile.
    import draft_writer
    topic = draft_writer.subject_topic(role)
    subject = (f"Recent NYU Graduate Inquiring about {topic} at "
               f"{draft_writer.display_company(company)}")
    bullets = _bullets(row)
    for contact in candidates:
        if recent_contacts.is_duplicate(contact["email"], subject):
            continue
        if dry_run:
            result["drafted"].append(contact["email"])
            continue
        try:
            gmail_client.create_draft(
                contact["email"], subject,
                _body(company, role, contact, bullets),
                attachments=[RESUME] if os.path.exists(RESUME) else None)
            result["drafted"].append(contact["email"])
        except Exception as exc:
            result["why"] = f"draft failed: {type(exc).__name__}"
    return result


def run(rows: list[dict] | None = None, limit: int = 10,
        want: int = CONTACTS_PER_APPLICATION, dry_run: bool = False) -> None:
    """Drafts for open applications that have no contact yet."""
    if rows is None:
        import datetime

        import findability
        from date_utils import parse_date
        _, missing = findability.coverage()
        # Newest first. An application from last spring is closed or forgotten;
        # the one logged this morning is the one worth a same-day email.
        rows = sorted(
            (r for r, _ in missing),
            key=lambda r: parse_date(r.get("Applied Draes", "")) or datetime.date.min,
            reverse=True)[:limit]
    rows = [r for r in rows if status_mod.is_open(r.get("Status", ""))][:limit]

    made = blocked = 0
    for row in rows:
        out = for_row(row, want=want, dry_run=dry_run)
        if out["drafted"]:
            made += len(out["drafted"])
            verb = "would draft" if dry_run else "drafted"
            print(f"  {out['company'][:26]:26s} {verb} "
                  f"{len(out['drafted'])}: {', '.join(out['drafted'])[:70]}")
        else:
            blocked += 1
            print(f"  {out['company'][:26]:26s} — {out['why']}")
    print(f"\n{'Would create' if dry_run else 'Created'} {made} draft(s) "
          f"across {len(rows) - blocked} application(s); {blocked} blocked.")
