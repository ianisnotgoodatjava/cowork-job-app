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
# Digits included: the second contact's fields are CONTACT_NAME_2 and
# friends, and a [A-Z_]+ class silently swallowed all of them into the
# first BODY instead of splitting them out.
_FIELD_RE = re.compile(r"^===\s*([A-Z_0-9]+)\s*===\s*$", re.M)

SYSTEM = """You research two contacts and write a cold outreach email to each, for Ian Liu.

WHO IAN IS
- B.A. Mathematics and Economics, NYU, December 2024. REDACTED.
- Currently Underwriting Assistant, General Property at REDACTED (Jun 2025-).
- A sales & trading summer internship in Taipei (Jun-Aug 2024): rates, EM FX
  and FX quant desks; daily market commentary; FX volatility with ARMA-GARCH;
  backtested G10 rates momentum and currency carry.
  Refer to this as markets or FX experience. Do NOT name the bank: he has
  asked twice not to, and "REDACTED" appears in generated drafts and in none
  of the 345 emails he has actually sent.
- REDACTED, Investment Banking Fall Analyst Intern (Aug-Dec 2023):
  project finance and infrastructure analysis, renewable energy and EV
  charging, capital raising materials.
- REDACTED, Data Operations Intern (Jun-Aug 2023): supervised-ML
  document pipelines in Python and JavaScript over 150,000 documents.
- Python, R, Excel, Tableau. Native English and Mandarin. US and Taiwan
  citizenship — authorised to work in the US, no sponsorship needed.

HOW HE WRITES
Plain, specific, unhurried. No exclamation marks, no "I am incredibly
passionate", no listing adjectives about himself.

{STYLE_PROFILE}

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

FIND THREE PEOPLE, NOT ONE
Give three different contacts at the company, each with their own address.
A single name is a single point of failure — people leave, go on parental
leave, or never read cold mail. Three tries at one company is the difference
between a reply and silence.

Spread them out: different teams, or different seniorities inside the
mid-level band, so the second and third are genuine second and third shots
rather than near-duplicates. Each email must have its OWN hook drawn from
that person's own work. Do not reuse a paragraph across them — colleagues
compare notes, and three identical emails read as a mail merge.

If you can only find one or two real people, fill those blocks and put
UNKNOWN in the rest. A real contact is worth more than an invented one, and
inventing the third is the one failure that cannot be recovered from.

OUTPUT — exactly this, nothing else. Repeat the CONTACT block for 1, 2 and 3:

=== JOB_URL ===
<direct URL to this exact job posting on the company's careers site or a job
board, or UNKNOWN. Must be the posting itself, not a careers landing page and
not a search results page. Verify it names this role before giving it.>
=== SOURCES ===
<one URL per line for every factual claim in any of the emails>
=== CONTACT_NAME_1 ===
<full name>
=== CONTACT_TITLE_1 ===
<exact job title>
=== CONTACT_EMAIL_1 ===
<address, or UNKNOWN>
=== CONFIDENCE_1 ===
<one line: how you found the address, and whether it is published or inferred>
=== SUBJECT_1 ===
<subject line — about them or their work, not about Ian>
=== BODY_1 ===
<the full email, starting "Dear <first name>," and ending with the sign-off>
=== CONTACT_NAME_2 ===
<full name, or UNKNOWN>
=== CONTACT_TITLE_2 ===
=== CONTACT_EMAIL_2 ===
=== CONFIDENCE_2 ===
=== SUBJECT_2 ===
=== BODY_2 ===
=== CONTACT_NAME_3 ===
<full name, or UNKNOWN>
=== CONTACT_TITLE_3 ===
=== CONTACT_EMAIL_3 ===
=== CONFIDENCE_3 ===
=== SUBJECT_3 ===
=== BODY_3 ===

If you cannot find a real named person at this company, put UNKNOWN in
CONTACT_NAME_1 and leave every SUBJECT and BODY empty. Do not invent a person."""


def _parse(text: str) -> dict:
    """Splits the delimited response into fields."""
    parts, out = _FIELD_RE.split(text), {}
    # split() yields [preamble, KEY, value, KEY, value, ...]
    for i in range(1, len(parts) - 1, 2):
        out[parts[i].strip().lower()] = parts[i + 1].strip()
    return out


def _via_cli(system: str, prompt: str, timeout: int = 420) -> str:
    """Run the same request through the `claude` CLI instead of the API.

    The API path needs ANTHROPIC_API_KEY, which is a second bill on top of a
    Claude subscription that is already paid for. The CLI is signed in to
    that subscription and can be driven headlessly, so the only difference
    is which credential does the work -- the prompt, the parsing, the
    address check and the style gate are all unchanged.

    WebSearch has to be granted explicitly. Without --allowed-tools the
    headless run has no search, and this whole step exists to look people up.
    """
    import subprocess

    result = subprocess.run(
        ["claude", "-p", prompt,
         "--append-system-prompt", system,
         "--allowed-tools", "WebSearch,WebFetch",
         "--model", MODEL],
        capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:200]
        if "not logged in" in detail.lower() or "/login" in detail:
            raise SystemExit(
                "The claude CLI is installed but not signed in.\n"
                "Run `claude` in a terminal, then `/login`, and this step\n"
                "works on your existing subscription with no API key.")
        raise RuntimeError(f"claude CLI failed: {detail}")
    return result.stdout


def _have_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _cli_ready() -> bool:
    """Whether the claude CLI exists and is signed in."""
    import shutil
    import subprocess
    if not shutil.which("claude"):
        return False
    try:
        out = subprocess.run(["claude", "-p", "ok"], capture_output=True,
                             text=True, timeout=60)
    except Exception:
        return False
    return "not logged in" not in (out.stdout + out.stderr).lower()


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

    # The measured style is injected here rather than baked into SYSTEM, so
    # rebuilding the corpus after more mail goes out moves the target instead
    # of leaving a stale description in a constant.
    import style_profile
    measured = style_profile.prompt_block()
    # Two real emails on the nearest topic, appended after the measured
    # profile. The numbers fix structure; only seeing real ones conveys
    # rhythm, and prose descriptions of rhythm produced the stilted register
    # these drafts had.
    examples = style_profile.exemplar_block(
        company=company, role=role,
        area=(row.get("Required Skills & Experience") or "")[:200])
    system = SYSTEM.replace("{STYLE_PROFILE}", "\n\n".join(
        part for part in (measured, examples) if part) or
        'He asks for 15-20 minutes and signs off "Best regards, Ian Liu".')

    # No key means no client, and the CLI does the same work on the
    # subscription instead. Everything after this point is identical either
    # way: same parse, same address check, same style gate.
    if client is None:
        text = _via_cli(system, prompt)
        fields = _parse(text)
        fields["_raw"] = text
        return fields

    # Streaming because web search plus a full email routinely runs long
    # enough to trip the non-streaming HTTP timeout.
    with client.messages.stream(
        model=MODEL,
        max_tokens=8000,
        system=system,
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
    # Either credential will do. The API bills separately; the CLI runs on
    # the Claude subscription already being paid for, so it is tried second
    # only because the API is faster when a key exists.
    client = None
    if _have_api_key():
        try:
            import anthropic
        except ImportError:
            raise SystemExit("pip install anthropic")
        client = anthropic.Anthropic()
    elif not _cli_ready():
        raise SystemExit(
            "No way to reach Claude, so the research step cannot run.\n"
            "\n"
            "Either:\n"
            "  run `claude` in a terminal and then `/login`  (no extra cost —\n"
            "  uses the subscription you already pay for), or\n"
            "  put ANTHROPIC_API_KEY in .env  (a few cents per application)."
        )

    import batch_outreach
    import findability

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
        # Collect whichever of the three contacts came back real. Looping
        # rather than repeating the block per contact: this was written twice
        # for two contacts, and a third copy is where they drift apart.
        import address_book
        resume = os.path.expanduser("~/Ian_Liu_Resume.pdf")
        contacts, seen_addr = [], set()
        for i in (1, 2, 3):
            name = fields.get(f"contact_name_{i}", "").strip()
            addr = fields.get(f"contact_email_{i}", "").strip()
            body = fields.get(f"body_{i}", "").strip()
            if not name or name.upper() == "UNKNOWN" or "@" not in addr:
                continue
            if addr.lower() in seen_addr or not body:
                continue

            # Check the address against what has actually been delivered at
            # that domain before it becomes a draft. A third of this mailbox's
            # outreach never arrived, and the format that works was already in
            # the delivery history each time: websterbank.com takes
            # initial+last, so chris.ferrara@ was doomed and cferrara@ was not.
            verdict = address_book.check(addr)
            if verdict.startswith("BOUNCED BEFORE"):
                print(f"    {addr} has bounced before — dropped")
                continue
            if verdict.startswith("MISMATCH"):
                print(f"    ! {verdict[:96]}")

            # Style gate. The generator's drift from the corpus was blunt and
            # measurable -- an invented "icl2016@nyu.edu | REDACTED"
            # signature on every draft against 0 of 345 real emails -- so it
            # is reported here rather than found in Drafts a week later.
            import style_check
            for problem in style_check.check(
                    body, fields.get(f"subject_{i}", "")):
                print(f"    style: {problem[:94]}")

            seen_addr.add(addr.lower())
            contacts.append({
                "name": name, "addr": addr, "verdict": verdict,
                "title": fields.get(f"contact_title_{i}", "").strip(),
                "confidence": fields.get(f"confidence_{i}", "").strip(),
                "subject": fields.get(f"subject_{i}", "").strip(),
                "body": body,
            })

        if not contacts:
            print("    no named person found — skipped rather than invented")
            continue

        path = os.path.join(
            out_dir,
            f"{row['row_number']:03d}_{batch_outreach._slug(row['Company'])}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Company:    {row['Company']}  (sheet row {row['row_number']})\n"
                    f"Role:       {row['Position']}\n"
                    f"Job link:   {fields.get('job_url', 'UNKNOWN')}\n"
                    f"\nSources:\n{fields.get('sources', '(none given)')}\n")
            for n, c in enumerate(contacts, 1):
                f.write(f"\n\nCONTACT {n} OF {len(contacts)}\n"
                        f"To:         {c['addr']}\n"
                        f"Person:     {c['name']} — {c['title']}\n"
                        f"Confidence: {c['confidence']}\n"
                        f"Address:    {c['verdict']}\n"
                        f"Subject:    {c['subject']}\n"
                        f"\n{'-' * 70}\n{c['body']}\n{'-' * 70}\n")

        # The posting URL is worth as much as the email: it is what the sheet
        # has almost none of, and every downstream step (prep package,
        # requirements, fit scoring) reads from it.
        job_url = fields.get("job_url", "").strip()
        if (write_link and not dry_run and job_url.startswith("http")
                and not (row.get("Job Link") or "").strip()):
            sheets.update_row(row["row_number"], {"Job Link": job_url})
            print(f"    job link -> sheet row {row['row_number']}")

        done.add(_key(row))
        for c in contacts:
            print(f"    {c['name']} — {c['title']}")
            print(f"      {c['addr']}  ({c['confidence'][:58]})")
        if len(contacts) < 3:
            print(f"    (only {len(contacts)} real contact(s) found — "
                  f"the rest were not invented)")
        print(f"    -> {path}")

        if create_drafts and not dry_run:
            import gmail_client
            for c in contacts:
                gmail_client.create_draft(
                    to=c["addr"], subject=c["subject"],
                    body_text=c["body"], attachments=[resume])
                print(f"    Gmail draft created (unsent) -> {c['addr']}")

    _mark_done(done)
    print(f"\nDone. Drafts in {out_dir}/")
    print("Read the SOURCES line on each before sending — the hook is only "
          "worth\nanything if it is actually true.")
