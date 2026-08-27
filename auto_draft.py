"""Researches a contact and writes a real outreach email, per application.

This is the piece `batch_outreach.py` deliberately leaves blank. That module
writes the skeleton and stops at [[WHO]] and [[HOOK]], because the nightly job
is plain Python against Gmail and Sheets — it has no way to look up who works
on a desk or read what they have published.

The Claude API closes that gap: the server-side web search tool runs the
research inside the same request that writes the email. So this module does
end to end what the good emails in this repo were written by hand:

  1. search for a real person at the company whose work touches the role,
  2. find something specific and checkable they have published or done,
  3. write the email in Ian's voice with that as the hook.

It never sends. Output is a Gmail *draft* or a file, same as everything else
here.

Two rules are pushed hard in the prompt because both were learned the
expensive way in this repo:

  * Never invent a quote or a claim about what someone said. An early
    hand-written draft attributed an argument to Paul Schell that he never
    made; it would have been obvious to him.
  * Prefer a mid-level practitioner over a department head. 18 emails to
    KBRA — several to senior people — produced zero replies, while every
    reply Ian has ever had came from someone mid-level.
"""

import json
import os
import re

import sheets
import status as status_mod

MODEL = "claude-opus-5"
OUT_DIR = os.path.join("logs", "drafts", "auto")

# Rows already researched. The watcher runs every few hours over the same
# sheet, and without this it would re-research — and re-bill — every open
# application on every tick.
DONE_FILE = os.path.join("logs", "auto_drafted.json")


def _done() -> set:
    try:
        with open(DONE_FILE) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def _mark_done(keys: set) -> None:
    os.makedirs(os.path.dirname(DONE_FILE), exist_ok=True)
    with open(DONE_FILE, "w") as f:
        json.dump(sorted(keys), f, indent=1)


def _key(row: dict) -> str:
    """Identity that survives the sheet being sorted and rows renumbering."""
    from company_match import _core, normalize
    company = _core(row.get("Company", "")).replace(" ", "")
    return f"{company}|{normalize(row.get('Position', ''))}"

# Delimited plain text rather than structured outputs. The response also
# carries web-search result blocks, and this runs unattended overnight where a
# parse failure is invisible — a format that degrades to "readable text a human
# can still use" is worth more here than a strict schema.
_FIELD_RE = re.compile(r"^===\s*([A-Z_]+)\s*===\s*$", re.M)

SYSTEM = """You research a contact and write one cold outreach email for Ian Liu.

WHO IAN IS
- B.A. Mathematics and Economics, NYU, December 2024. REDACTED.
- Currently Underwriting Assistant, General Property at REDACTED (Jun 2025-).
- REDACTED, Sales & Trading Summer Analyst, Taipei (Jun-Aug 2024):
  rotated Rates Trading, Emerging Markets FX and FX Quant desks; wrote daily
  market commentary; researched FX volatility with ARMA-GARCH models;
  backtested G10 rates momentum and currency carry strategies.
- REDACTED, Investment Banking Fall Analyst Intern (Aug-Dec 2023):
  project finance and infrastructure analysis, renewable energy and EV
  charging, capital raising materials.
- REDACTED, Data Operations Intern (Jun-Aug 2023): supervised-ML
  document pipelines in Python and JavaScript over 150,000 documents.
- Python, R, Excel, Tableau. Native English and Mandarin. US and Taiwan
  citizenship — authorised to work in the US, no sponsorship needed.

HOW HE WRITES
Plain, specific, unhurried. No exclamation marks, no "I am incredibly
passionate", no listing adjectives about himself. He asks for 15-20 minutes.
He signs off "Best regards, Ian Liu".

WHO TO PICK
Target a mid-level practitioner — Analyst, Senior Analyst, Associate
Director, Director, VP, or Principal — who does the work the role involves.
Do NOT target Chief/Head/Managing Director/Partner unless the firm is small
enough that nobody else exists. Heads do not reply. Every reply Ian has
received came from someone mid-level.
Prefer someone whose email address you can find published (rating agency
analytical contacts, press releases, staff pages). If you must infer the
address from the firm's pattern, say so in CONFIDENCE.

THE HOOK — THE ONLY PART THAT MATTERS
One paragraph referring to something specific and verifiable: a report they
authored, a deal they were the analytical contact on, a talk, a public career
move. Ask a real question about it that they could disagree with.

NEVER invent a quote, a statistic, or a claim about what the person argued.
If you only know a piece exists by title, refer to it by topic and frame your
interest as a question — do not assert what it concluded. A fabricated detail
is worse than a generic email because they will notice.

Do not mention that Ian applied to the role, unless the person holds the same
job title he applied for — with a peer it is the honest reason for writing.

OUTPUT — exactly this, nothing else:

=== CONTACT_NAME ===
<full name>
=== CONTACT_TITLE ===
<exact job title>
=== CONTACT_EMAIL ===
<address, or UNKNOWN>
=== CONFIDENCE ===
<one line: how you found the address, and whether it is published or inferred>
=== JOB_URL ===
<direct URL to this exact job posting on the company's careers site or a job
board, or UNKNOWN. Must be the posting itself, not a careers landing page and
not a search results page. Verify it names this role before giving it.>
=== SOURCES ===
<one URL per line for every factual claim in the email>
=== SUBJECT ===
<subject line — about them or their work, not about Ian>
=== BODY ===
<the full email, starting "Dear <first name>," and ending with the sign-off>

If you cannot find a real named person at this company, put UNKNOWN in
CONTACT_NAME and leave SUBJECT and BODY empty. Do not invent a person."""


def _parse(text: str) -> dict:
    """Splits the delimited response into fields."""
    parts, out = _FIELD_RE.split(text), {}
    # split() yields [preamble, KEY, value, KEY, value, ...]
    for i in range(1, len(parts) - 1, 2):
        out[parts[i].strip().lower()] = parts[i + 1].strip()
    return out


def draft_for_row(client, row: dict) -> dict:
    """Researches and writes one email. Returns the parsed fields."""
    company = (row.get("Company") or "").strip()
    role = (row.get("Position") or "").strip()
    skills = (row.get("Required Skills & Experience") or "").strip()

    prompt = (
        f"Company: {company}\n"
        f"Role Ian applied to: {role}\n"
        f"Location: {row.get('location') or 'unknown'}\n"
        f"Applied: {row.get('Applied Draes') or 'unknown'}\n"
        + (f"Posting requirements: {skills[:600]}\n" if skills else "")
        + "\nFind the live posting for this exact role, pick the right "
          "person, and write the email."
    )

    # Streaming because web search plus a full email routinely runs long
    # enough to trip the non-streaming HTTP timeout.
    with client.messages.stream(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        tools=[{
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": 8,
        }],
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        detail = getattr(message, "stop_details", None)
        return {"error": f"refused: {getattr(detail, 'category', 'unknown')}"}

    text = "\n".join(b.text for b in message.content if b.type == "text")
    fields = _parse(text)
    fields["_raw"] = text
    return fields


def run(limit: int = 5, min_fit: float = 2.0, dry_run: bool = True,
        out_dir: str = OUT_DIR, create_drafts: bool = False,
        new_only: bool = False, write_link: bool = True) -> None:
    try:
        import anthropic
    except ImportError:
        raise SystemExit("pip install anthropic")

    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise SystemExit(
            "No Anthropic credentials. Set ANTHROPIC_API_KEY in .env or the\n"
            "environment. This is the only step that costs money — roughly a\n"
            "few cents per email, mostly the web searches."
        )

    import batch_outreach
    import findability

    client = anthropic.Anthropic()
    rows = [r for r in sheets.list_rows()
            if status_mod.is_open(r.get("Status", ""))]
    done = _done()
    if new_only:
        rows = [r for r in rows if _key(r) not in done]

    # Findability first, fit second. Every row costs the same in web searches,
    # but they are not equally likely to yield anything: a rating agency
    # publishes its analysts' names and addresses outright, while a private
    # shop with no footprint burns the same eight searches and returns
    # UNKNOWN. Ordering by fit alone spent the budget on whichever role
    # matched the resume best, findable or not.
    patterns = findability._load_patterns()
    tier_rank = {t: i for i, t in enumerate(findability.ORDER)}

    def priority(row):
        tier, _ = findability.classify(row, patterns)
        return (tier_rank.get(tier, 99), -batch_outreach.fit_score(row))

    ranked = sorted(rows, key=priority)
    targets = [r for r in ranked
               if batch_outreach.fit_score(r) >= min_fit][:limit]

    if new_only and not targets:
        print("nothing new to research")
        return
    print(f"{len(rows)} {'new' if new_only else 'open'} application(s); "
          f"researching {len(targets)}, most findable first\n", flush=True)
    os.makedirs(out_dir, exist_ok=True)

    for row in targets:
        label = f"{row['Company'][:26]} | {row['Position'][:34]}"
        print(f"  researching {label} ...", flush=True)
        try:
            fields = draft_for_row(client, row)
        except Exception as exc:
            print(f"    ! {type(exc).__name__}: {str(exc)[:140]}")
            continue

        if fields.get("error"):
            print(f"    ! {fields['error']}")
            continue
        name = fields.get("contact_name", "").strip()
        if not name or name.upper() == "UNKNOWN":
            print("    no named person found — skipped rather than invented")
            continue

        # Check the address against what has actually been delivered at that
        # domain before it becomes a draft. A third of this mailbox's outreach
        # never arrived, and the format that works was already sitting in the
        # delivery history each time: websterbank.com takes initial+last, so
        # chris.ferrara@ was doomed and cferrara@ was not. Research picks the
        # person; this decides whether the address is worth trusting.
        import address_book
        addr = fields.get("contact_email", "").strip()
        verdict = address_book.check(addr) if "@" in addr else "no address given"
        fields["_verdict"] = verdict
        if verdict.startswith("BOUNCED BEFORE"):
            print(f"    {addr} has bounced before — not drafting to it again")
            continue
        if verdict.startswith("MISMATCH"):
            print(f"    ! {verdict[:96]}")

        path = os.path.join(
            out_dir,
            f"{row['row_number']:03d}_{batch_outreach._slug(row['Company'])}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"To:         {fields.get('contact_email', 'UNKNOWN')}\n"
                    f"Person:     {name} — {fields.get('contact_title', '')}\n"
                    f"Confidence: {fields.get('confidence', '')}\n"
                    f"Address:    {fields.get('_verdict', '')}\n"
                    f"Company:    {row['Company']}  (sheet row {row['row_number']})\n"
                    f"Role:       {row['Position']}\n"
                    f"Job link:   {fields.get('job_url', 'UNKNOWN')}\n"
                    f"Subject:    {fields.get('subject', '')}\n"
                    f"\nSources:\n{fields.get('sources', '(none given)')}\n"
                    f"\n{'-' * 70}\n{fields.get('body', '')}\n{'-' * 70}\n")

        # The posting URL is worth as much as the email: it is what the sheet
        # has almost none of, and every downstream step (prep package,
        # requirements, fit scoring) reads from it.
        job_url = fields.get("job_url", "").strip()
        if (write_link and not dry_run and job_url.startswith("http")
                and not (row.get("Job Link") or "").strip()):
            sheets.update_row(row["row_number"], {"Job Link": job_url})
            print(f"    job link -> sheet row {row['row_number']}")

        done.add(_key(row))
        print(f"    {name} — {fields.get('contact_title', '')}")
        print(f"    {fields.get('contact_email', 'UNKNOWN')}  "
              f"({fields.get('confidence', '')[:60]})")
        print(f"    -> {path}")

        if create_drafts and not dry_run:
            body = fields.get("body", "").strip()
            addr = fields.get("contact_email", "").strip()
            if body and "@" in addr:
                import gmail_client
                gmail_client.create_draft(
                    to=addr,
                    subject=fields.get("subject", "").strip(),
                    body_text=body,
                    attachments=[os.path.expanduser("~/Ian_Liu_Resume.pdf")],
                )
                print("    Gmail draft created (unsent)")

    _mark_done(done)
    print(f"\nDone. Drafts in {out_dir}/")
    print("Read the SOURCES line on each before sending — the hook is only "
          "worth\nanything if it is actually true.")
