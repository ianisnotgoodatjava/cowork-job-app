"""Turns a researched contact plus a hook into a finished, sendable email.

`batch_outreach` writes the scaffolding and stops at [[WHO]] and [[HOOK]],
because plain Python cannot find out who works on a desk. `auto_draft` fills
those in through the Claude API. This sits between them: the research and the
hook come in as arguments — from the API, or from a person who did the
reading — and everything mechanical is handled here.

Keeping the boilerplate in one place matters more than it looks. The parts of
a cold email that are identical across thirty companies are exactly the parts
that go stale or inconsistent when they are retyped thirty times; the hook is
the only part that should differ, so it is the only part this asks for.

Nothing is sent. Output is a file, and optionally an unsent Gmail draft.
"""

import json
import os
import re

import sheets

OUT_DIR = os.path.join("logs", "drafts", "ready")
CONTACTS_FILE = os.path.join("logs", "contacts_found.json")
RESUME = os.path.expanduser("~/Ian_Liu_Resume.pdf")

SIGNATURE = "Best regards,\nIan Liu\nicl2016@nyu.edu | REDACTED"


# Your subject line, as you actually write it: "Recent NYU Graduate Inquiring
# about ABS at Fitch Ratings" — 381 sent messages use this exact opening. The
# topic is a short subject area, never the posting's full title, so
# "Structured Finance - Asset-Backed Securities, Analyst - New York" has to be
# cut down to what the desk actually does.
SUBJECT_PREFIX = "Recent NYU Graduate Inquiring about"

# Seniority and shape, not subject matter. Removing these is what turns a
# posting title into a topic.
_ROLE_NOISE_RE = re.compile(
    r"\b(senior|junior|jr\.?|sr\.?|entry[- ]level|graduate|campus|"
    r"assistant|associate|analyst|analytics professional|intern(ship)?|"
    r"manager|director|vice president|vp|officer|representative|rep|"
    r"specialist|consultant|coordinator|generalist|trainee|program(me)?|"
    r"rotational?|full[- ]time|part[- ]time|i{1,3}|20\d\d)\b",
    re.I)

# Location tails and requisition ids.
_PLACE_RE = re.compile(
    r"\s*[-–(,]\s*(new york|nyc|ny|charlotte|boston|chicago|london|remote|"
    r"united states|us|usa|bethesda|stamford|jersey city|taipei)\b[^,]*",
    re.I)
_REQ_RE = re.compile(r"\s*[-–(#]?\s*(req(uisition)?\.?\s*)?\b\d{4,}\b\)?", re.I)


def subject_topic(role: str, fallback: str = "Research") -> str:
    """"Revenue Economics Analyst" -> "Revenue Economics"."""
    text = _REQ_RE.sub("", role or "")
    text = _PLACE_RE.sub("", text)
    # Segments, longest-first: the substantive part of a title is usually the
    # longest one once the seniority words are gone.
    segments = [seg.strip(" -–—/") for seg in re.split(r"[,/]|\s[-–—]\s", text)]
    cleaned = []
    for seg in segments:
        without = _ROLE_NOISE_RE.sub(" ", seg)
        without = re.sub(r"\s{2,}", " ", without).strip(" -–—,&")
        if len(without) >= 3:
            cleaned.append(without)
    if not cleaned:
        return fallback
    return max(cleaned, key=len)


def display_company(name: str) -> str:
    """How you write the company in a subject line: "Bright Power", not
    "BRIGHT POWER INC". Import spellings shout; yours do not."""
    name = (name or "").strip()
    name = re.sub(r"[,\s]+(inc|llc|l\.l\.c|ltd|corp|co)\.?$", "", name,
                  flags=re.I).strip(" ,.")
    words = name.split()
    # A single all-caps token is an acronym, not shouting: SIFMA and KBRA are
    # how those firms write themselves, and "Sifma" would look like a typo.
    if len(words) == 1:
        if name.isupper() and len(name) <= 6:
            return name
        if name.islower():
            return name[:1].upper() + name[1:]
        return name
    letters = [c for c in name if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        # Multi-word shouting gets title case, but short all-caps tokens
        # inside it (PT, US, ETF) are acronyms and stay.
        return " ".join(w if len(w) <= 3 and w.isupper() else w.title()
                        for w in words)
    return name


def subject_for(row: dict, fallback: str = "Research") -> str:
    company = display_company(row.get("Company", ""))
    topic = subject_topic(row.get("Position", ""), fallback)
    return f"{SUBJECT_PREFIX} {topic} at {company}"


def compose(contact: dict, row: dict, hook: str, ask: str = "") -> str:
    """The full email body. `hook` is the only part that is not boilerplate."""
    first = (contact["name"].split() or ["there"])[0]
    company = (row.get("Company") or "").strip()
    role = (row.get("Position") or "").strip()

    opening = (
        f"My name is Ian Liu. I graduated from NYU in December 2024 with a "
        f"degree in Mathematics and Economics, and I currently work as an "
        f"Underwriting Assistant at REDACTED. I recently applied for the "
        f"{role} role at {company}, and I wanted to reach out to you directly."
    )
    close = (
        ask or
        "If you have 15-20 minutes in the coming weeks, I would be grateful "
        "for the chance to hear how you got into the work and how your team "
        "approaches it. I have attached my resume for reference."
    )
    return (f"Dear {first},\n\n{opening}\n\n{hook.strip()}\n\n{close}\n\n"
            f"Thank you for your time.\n\n{SIGNATURE}\n")


def _address_note(addr: str) -> str:
    try:
        import address_book
        return address_book.check(addr)
    except Exception as exc:
        return f"(address history unavailable: {type(exc).__name__})"


def _existing_drafts() -> set:
    """{(to, subject)} already sitting in Gmail.

    Re-running the generator used to create a second copy of every draft it
    had made before, because nothing checked. Drafts are cheap to create and
    cannot be deleted from here, so the check belongs before the call.
    """
    try:
        from googleapiclient.discovery import build
        from google_auth import get_credentials
        svc = build("gmail", "v1", credentials=get_credentials())
        listed = svc.users().drafts().list(
            userId="me", maxResults=300).execute().get("drafts", [])
        seen = set()
        for d in listed:
            msg = svc.users().drafts().get(
                userId="me", id=d["id"], format="metadata").execute()
            headers = {h["name"]: h["value"]
                       for h in msg["message"]["payload"]["headers"]}
            seen.add((headers.get("To", "").strip().lower(),
                      headers.get("Subject", "").strip()))
        return seen
    except Exception as exc:
        print(f"    (could not read existing drafts: {type(exc).__name__})")
        return set()


def write(row: dict, contacts: list[dict], out_dir: str = OUT_DIR,
          create_drafts: bool = False,
          existing: set | None = None) -> list[str]:
    """Writes one file per contact, and optionally the Gmail drafts."""
    os.makedirs(out_dir, exist_ok=True)
    import batch_outreach
    slug = batch_outreach._slug(row.get("Company", ""))
    written = []

    for n, c in enumerate(contacts, 1):
        body = compose(c, row, c["hook"], c.get("ask", ""))
        note = _address_note(c["email"])
        path = os.path.join(out_dir, f"{row['row_number']:03d}_{slug}_{n}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                f"To:        {c['email']}\n"
                f"Person:    {c['name']} — {c['title']}\n"
                f"Source:    {c.get('source', '')}\n"
                f"Address:   {c.get('confidence', '')} | history: {note}\n"
                f"Company:   {row.get('Company')}  (sheet row {row['row_number']})\n"
                f"Role:      {row.get('Position')}\n"
                f"Job link:  {row.get('Job Link') or 'unknown'}\n"
                f"Subject:   {c['subject']}\n"
                f"\n{'-' * 72}\n{body}{'-' * 72}\n")
        written.append(path)

        if create_drafts:
            key = (c["email"].strip().lower(), c["subject"].strip())
            if existing is not None and key in existing:
                print(f"    already in Gmail, not duplicated: {c['email']}")
                continue
            import gmail_client
            attachments = [RESUME] if os.path.exists(RESUME) else []
            gmail_client.create_draft(to=c["email"], subject=c["subject"],
                                      body_text=body, attachments=attachments)
            if existing is not None:
                existing.add(key)
    return written


def run(create_drafts: bool = False, contacts_file: str = CONTACTS_FILE) -> None:
    """Generates every draft described in the contacts file."""
    with open(contacts_file) as f:
        found = json.load(f)
    existing = _existing_drafts() if create_drafts else None

    # Keyed by company, never by row number. The sheet is re-sorted and
    # de-duplicated on every scheduled pass, so a row number recorded today
    # points at a different application tomorrow — which would have sent one
    # company's contacts an email about another company's job.
    import status as status_mod
    from company_match import _core
    from date_utils import parse_date
    import datetime

    by_company: dict[str, dict] = {}
    for r in sheets.list_rows():
        key = _core(r.get("Company", "")).replace(" ", "")
        if not key:
            continue
        best = by_company.get(key)
        # Prefer an application that is still open, then the most recent.
        rank = (status_mod.is_open(r.get("Status", "")),
                parse_date(r.get("Applied Draes", "")) or datetime.date.min)
        if best is None or rank > best[0]:
            by_company[key] = (rank, r)

    total, made = 0, 0
    for key, contacts in found.items():
        entry = by_company.get(_core(key).replace(" ", ""))
        if entry is None:
            print(f"  {key}: no application on the sheet — skipped")
            continue
        row = entry[1]
        paths = write(row, contacts, create_drafts=create_drafts,
                      existing=existing)
        made += 1
        total += len(paths)
        print(f"  row {row['row_number']:>3} | {row['Company'][:26]:26s} | "
              f"{len(paths)} draft(s)")
        for c in contacts:
            print(f"        {c['name']} — {c['title']}  <{c['email']}>")

    print(f"\n{total} draft(s) across {made} application(s) -> {OUT_DIR}/")
    if create_drafts:
        print("Also created as unsent Gmail drafts.")
    else:
        print("Files only. Re-run with create_drafts=True to put them in Gmail.")
