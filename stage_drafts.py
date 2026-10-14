"""Turns the research auto_draft already saved into a contact on the sheet
and a draft in Gmail.

auto_draft writes the posting URL back to the sheet but never the person it
found, so the Contact column stays empty on exactly the rows that just had a
contact researched. And when a run is interrupted -- a rate limit, a quota
hit, a closed laptop -- the .txt files survive while the Gmail drafts never
get made, with no way to finish the job short of re-running the research.

Both are the same missing step: the expensive part is on disk, and this
reads it rather than paying for it again.

Nothing is sent. Drafts are drafts, and a Contact cell that already names
someone is left alone -- a person he has actually spoken to outranks an
address a web search inferred.
"""

import os
import re

from config import ME_RE

DRAFT_DIR = os.path.join("logs", "drafts", "auto")

_ROW_RE = re.compile(r"^Company:\s+(.*?)\s+\(sheet row (\d+)\)", re.M)
_CONTACT_RE = re.compile(
    r"^To:\s+(?P<addr>\S+)\n"
    r"^Person:\s+(?P<name>.*?)\s+[—-]\s+(?P<title>.*?)\n"
    r"^Confidence:\s+(?P<confidence>.*?)\n"
    r"^Address:\s+(?P<verdict>.*?)\n"
    r"^Subject:\s+(?P<subject>.*?)\n"
    r"\n-{60,}\n(?P<body>.*?)\n-{60,}",
    re.M | re.S)


def parse(path: str) -> dict | None:
    """One saved research file -> {company, row_number, contacts[]}."""
    with open(path, encoding="utf-8") as f:
        text = f.read()

    head = _ROW_RE.search(text)
    if not head:
        return None

    contacts = [m.groupdict() for m in _CONTACT_RE.finditer(text)]
    if not contacts:
        return None

    return {
        "company": head.group(1),
        "row_number": int(head.group(2)),
        "contacts": contacts,
        "path": path,
    }


def load_all(draft_dir: str = DRAFT_DIR) -> list[dict]:
    """Every parsed research file, newest first. SKIPPED files hold no
    contact by definition and are ignored."""
    if not os.path.isdir(draft_dir):
        return []
    found = []
    for name in sorted(os.listdir(draft_dir)):
        if not name.endswith(".txt") or ".SKIPPED." in name:
            continue
        parsed = parse(os.path.join(draft_dir, name))
        if parsed:
            found.append(parsed)
    found.sort(key=lambda p: os.path.getmtime(p["path"]), reverse=True)
    return found


def address_confidence(contact: dict) -> str:
    """How much the address can be trusted: delivered / published / inferred.

    Worth surfacing per contact, because a bounce is the one outcome worse
    than sending nothing -- it burns the contact without reaching anyone.

    Three signals, strongest first. Mail already delivered to that domain
    pattern beats any prose claim, and it is the only one of the three that
    is evidence rather than assertion. Then an address published by the firm
    itself. Everything else is a pattern guess.

    Reading the prose needs care: "Name and title are published ... the
    address is INFERRED" is an inferred address, and "not inferred" is a
    published one, so a bare substring test for "published" or "inferred"
    gets both backwards.
    """
    confidence = (contact.get("confidence") or "").lower()
    verdict = (contact.get("verdict") or "").lower()

    delivered = re.search(r"(\d+)\s+delivered", verdict)
    if delivered:
        return f"{delivered.group(1)} delivered"
    if "not inferred" in confidence or confidence.startswith("published"):
        return "published"
    return "inferred"


def _locate(item: dict, by_number: dict, by_company: dict, slug) -> dict | None:
    """The sheet row this research belongs to, identified by company.

    The saved row number is only believed when the row still holds the same
    company. Otherwise the company decides, and an ambiguous company -- Fitch
    has several open roles -- resolves to the row that still has no contact,
    so repeat runs fill them one at a time rather than all landing on the
    first.
    """
    company = slug(item["company"])
    stored = by_number.get(item["row_number"])
    if stored and slug(stored.get("Company", "")) == company:
        return stored

    matches = by_company.get(company, [])
    if not matches:
        return None
    empty = [r for r in matches if not (r.get("Contact") or "").strip()]
    return (empty or matches)[0]


def run(draft_dir: str = DRAFT_DIR, dry_run: bool = True,
        write_sheet: bool = True, create_drafts: bool = False,
        resume: str | None = None) -> None:
    # Imported here, not at module scope: sheets reads the spreadsheet id at
    # import time, so a direct run has to get .env loaded first.
    import sheets
    from company_match import slug

    # The row number saved in the research file is a position, not an
    # identity, and `sort-by-date` renumbers every row beneath an insert.
    # Research saved in September and staged in October wrote CoStar's
    # contact onto CHAMP and Douglas Elliman's onto Titan Partners that way.
    # Match on the company instead, and only trust the stored number when it
    # still points at the same company.
    all_rows = sheets.list_rows()
    by_number = {r["row_number"]: r for r in all_rows}
    by_company = {}
    for row in all_rows:
        by_company.setdefault(slug(row.get("Company", "")), []).append(row)
    staged = load_all(draft_dir)
    if resume:
        staged = [s for s in staged
                  if re.search(resume, s["company"], re.I)]
    if not staged:
        print(f"No researched drafts found in {draft_dir}.")
        return

    print(f"{len(staged)} researched application(s) on disk.\n")

    sheet_patches, to_draft = {}, []
    for item in staged:
        row = _locate(item, by_number, by_company, slug)
        existing = (row.get("Contact") or "").strip() if row else ""
        # His own address lands in this column from time to time -- a send
        # recorded from the wrong side of the From header. It is not a
        # contact and must not be the thing that blocks a real one.
        if existing and ME_RE.search(existing):
            existing = ""
        for contact in item["contacts"]:
            print(f"  {item['company'][:24]:24s} | {contact['name'][:20]:20s} "
                  f"| {contact['addr'][:32]:32s} | "
                  f"{address_confidence(contact)}")
            to_draft.append((item, contact))

        if not row:
            print(f"    no sheet row for {item['company'][:30]} any more")
            continue
        if existing:
            print(f"    sheet already names {existing[:46]} — left alone")
            continue
        first = item["contacts"][0]
        sheet_patches[row["row_number"]] = {
            "Contact": f"{first['name']} <{first['addr']}>"
        }

    print()
    if write_sheet:
        if dry_run:
            print(f"Would name the contact on {len(sheet_patches)} sheet row(s).")
        else:
            for row_number, patch in sheet_patches.items():
                sheets.update_row(row_number, patch)
            print(f"Named the contact on {len(sheet_patches)} sheet row(s).")

    if create_drafts:
        if dry_run:
            print(f"Would create {len(to_draft)} Gmail draft(s).")
        else:
            import gmail_client
            made = 0
            for item, contact in to_draft:
                try:
                    gmail_client.create_draft(
                        to=contact["addr"],
                        subject=contact["subject"],
                        body_text=contact["body"],
                    )
                    made += 1
                    print(f"  drafted -> {contact['addr']}")
                except Exception as exc:
                    print(f"  ! {contact['addr']}: "
                          f"{type(exc).__name__}: {str(exc)[:90]}")
            print(f"\n{made} draft(s) in Gmail, unsent.")

    if dry_run:
        print("\nDry run. Add --write to apply.")


if __name__ == "__main__":
    import sys

    # cli.py loads this for every other module; a direct run has to do it
    # itself or sheets comes up with no spreadsheet id.
    from dotenv import load_dotenv
    load_dotenv()

    run(dry_run="--write" not in sys.argv,
        create_drafts="--drafts" in sys.argv)
