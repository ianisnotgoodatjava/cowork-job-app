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
import time

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


def _via_cli(system: str, prompt: str, timeout: int = 1200) -> str:
    """Run the same request through the `claude` CLI instead of the API.

    The API path needs ANTHROPIC_API_KEY, which is a second bill on top of a
    Claude subscription that is already paid for. The CLI is signed in to
    that subscription and can be driven headlessly, so the only difference
    is which credential does the work -- the prompt, the parsing, the
    address check and the style gate are all unchanged.

    WebSearch has to be granted explicitly. Without --allowed-tools the
    headless run has no search, and this whole step exists to look people up.

    Two failures are worth waiting out rather than giving up on. A request
    that runs Opus through eight web searches streams for a minute or more,
    and the connection dropping mid-stream is ordinary -- the same request
    succeeds on the next try. And the subscription's usage window is shared
    with interactive Claude Code sessions, so a 429 here often means "the
    window is full right now", not "never". Anything else is real and is
    raised as before.
    """
    import subprocess

    # Disallowing Task is deliberate. Left to itself the CLI spawns a research
    # subagent that runs twenty-odd searches per contact -- the API path was
    # designed around max_uses=8 -- and one such run consumed the whole
    # subscription usage window before it could finish writing. The
    # Only Task and Bash are named. The CLI refuses the whole command if a
    # deny rule names a tool it does not have, and the tool set differs
    # between versions -- "MultiEdit" once cost a full batch that way. Task
    # is the one that multiplies cost; Bash is the one with no place here.
    # The file-editing tools are harmless to a prompt that never asks for
    # them, so they are left alone rather than guessed at.
    argv = ["claude", "-p", prompt,
            "--append-system-prompt", system,
            "--allowed-tools", "WebSearch,WebFetch",
            "--disallowed-tools", "Task,Bash",
            "--model", MODEL]
    transient = ("connection closed", "rate limit", "429",
                 "try again later", "overloaded", "timed out",
                 "unable to connect", "enotfound", "econnreset",
                 "etimedout", "network")
    max_attempts = 4

    # A clean environment for the child. When this runs from inside an
    # interactive Claude Code session, the subprocess inherits ~27 session
    # variables -- CLAUDECODE=1, CLAUDE_CODE_CHILD_SESSION=1, the messaging
    # socket, the session id -- and reads as a nested child of that session,
    # picking up its permission rules. One such run failed outright with
    # "Permission deny rule 'MultiEdit' matches no known tool". The launchd
    # job never has any of that, so the two paths behaved differently.
    # Keep only what the CLI needs: the credential, PATH, HOME.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE")}

    for attempt in range(1, max_attempts + 1):
        try:
            result = subprocess.run(argv, capture_output=True, text=True,
                                    timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            detail = f"timed out after {timeout}s"
        else:
            if result.returncode == 0:
                return result.stdout
            detail = (result.stderr or result.stdout or "").strip()[:200]

        lowered = detail.lower()
        if "not logged in" in lowered or "/login" in detail:
            raise SystemExit(
                "The claude CLI is installed but not signed in.\n"
                "Run `claude` in a terminal, then `/login`, and this step\n"
                "works on your existing subscription with no API key.")
        if not any(marker in lowered for marker in transient):
            raise RuntimeError(f"claude CLI failed: {detail}")
        # A 429 that says the request "would exceed" the limit is a size or
        # quota ceiling, not a burst that clears in a minute. Retrying it
        # spent four more attempts of quota on an identical request and
        # tripped the account's usage limit twice on fresh resets. One try,
        # then hand it to the next scheduled run.
        if "would exceed" in lowered or "usage limit" in lowered:
            raise RuntimeError(
                f"claude CLI rate-limited: {detail[:120]}\n"
                "    Not retrying -- this is a quota ceiling, and the next "
                "scheduled pass\n    will pick the row up when the window "
                "resets.")
        if attempt == max_attempts:
            raise RuntimeError(f"claude CLI failed after {max_attempts} "
                               f"attempts: {detail}")

        # Usage-window 429s clear on the window's own schedule, so the wait
        # is minutes, not seconds; a dropped connection needs almost none.
        wait = 120 * attempt if "429" in lowered or "rate limit" in lowered \
            else 10 * attempt
        print(f"    {detail[:60]} — retrying in {wait}s "
              f"(attempt {attempt}/{max_attempts})", flush=True)
        time.sleep(wait)


def _credential() -> str:
    """Which credential is available, and therefore which path to take.

    The two are not interchangeable, which cost a full debugging pass to
    learn. A metered API key (sk-ant-api03-) reaches every model over the
    Messages API. A subscription OAuth token (sk-ant-oat01-, what
    `claude setup-token` prints) does not: over the raw API it is throttled
    to Haiku, and every Opus and Sonnet request comes back 429 no matter how
    long you back off. The same token driving the `claude` CLI runs Opus
    with web search perfectly well, because that path bills against the
    subscription's own entitlements instead.

    So the token is not the deciding factor -- its *type* is.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")

    # An oat01 token in either variable means CLI, not API.
    for value in (key, token):
        if value.startswith("sk-ant-oat01-"):
            return "cli"
    if key or token:
        return "api"
    return "none"


def _have_api_key() -> bool:
    return _credential() == "api"


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


def _stream_with_retry(client, max_attempts: int = 6, **kwargs):
    """Runs client.messages.stream(**kwargs), retrying on 429s.

    A batch of 10 sequential single-row requests against the subscription
    (OAuth) credential hit RateLimitError on every single one — that
    credential's per-minute limit is far tighter than a metered API key's,
    and nothing here was slowing down between requests to respect it. This
    honors the API's Retry-After header when it sends one, and otherwise
    backs off exponentially, so a batch degrades to "slower" instead of
    "produces nothing."
    """
    import anthropic

    for attempt in range(1, max_attempts + 1):
        try:
            with client.messages.stream(**kwargs) as stream:
                return stream.get_final_message()
        except anthropic.RateLimitError as exc:
            if attempt == max_attempts:
                raise
            retry_after = None
            response = getattr(exc, "response", None)
            if response is not None:
                header = response.headers.get("retry-after")
                if header:
                    try:
                        retry_after = float(header)
                    except ValueError:
                        pass
            wait = retry_after or min(60, 5 * 2 ** (attempt - 1))
            print(f"    rate limited — waiting {wait:.0f}s "
                  f"(attempt {attempt}/{max_attempts})", flush=True)
            time.sleep(wait)


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

    # The subscription path has a much tighter rate window than a metered
    # key. The full job -- three contacts, up to eight searches each, results
    # accumulating into context -- was rejected outright with "this request
    # would exceed your account's rate limit", twice, on fresh resets. One
    # contact and three searches fits; the output format is unchanged so the
    # parser and the address check downstream do not care.
    if client is None:
        prompt += (
            "\n\nBUDGET FOR THIS RUN: find ONE contact only and use at most "
            "3 web searches. Fill CONTACT block 1; put UNKNOWN in "
            "CONTACT_NAME_2 and CONTACT_NAME_3 and leave their other fields "
            "empty. Keep thinking brief."
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
    message = _stream_with_retry(
        client,
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
    )

    if message.stop_reason == "refusal":
        detail = getattr(message, "stop_details", None)
        return {"error": f"refused: {getattr(detail, 'category', 'unknown')}"}

    text = "\n".join(b.text for b in message.content if b.type == "text")
    fields = _parse(text)
    fields["_raw"] = text
    return fields


def run(limit: int = 5, min_fit: float = 2.0, dry_run: bool = True,
        out_dir: str = OUT_DIR, create_drafts: bool = False,
        new_only: bool = False, write_link: bool = True,
        days_back: int | None = None,
        skip_interviewing: bool = True,
        prefer: str | None = None,
        exclude: str | None = None) -> None:
    # The credential's type picks the path -- see _credential(). A metered
    # key goes over the API because it is faster; a subscription token has
    # to go through the CLI, because over the API it is capped at Haiku,
    # which cannot use the web search this step is built around.
    client = None
    if _credential() == "api":
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

    # A company you are already interviewing at does not need a cold email
    # to a stranger on the desk -- it needs the interview to go well. And
    # findability ranks the same well-documented firms first every time, so
    # without a date window the recent applications never come up at all.
    if skip_interviewing:
        rows = [r for r in rows
                if "interview" not in (r.get("Status") or "").lower()]
    # A staffing agency is not the employer. Researching a "Senior Analyst"
    # at Onyx Staffing finds a recruiter, and the email reads as one more
    # candidate chasing them -- which is what they already have a queue of.
    rows = [r for r in rows if not re.search(
        r"staffing|recruit|talent partners|search partners|\bagency\b",
        r.get("Company") or "", re.I)]
    if days_back:
        import datetime
        from date_utils import parse_date
        cutoff = datetime.date.today() - datetime.timedelta(days=days_back)
        rows = [r for r in rows
                if (parse_date(r.get("Applied Draes", "")) or datetime.date.min)
                >= cutoff]
    # A firm he has said not to contact is out regardless of how it ranks.
    if exclude:
        rows = [r for r in rows
                if not re.search(exclude, r.get("Company") or "", re.I)]
    done = _done()
    if new_only:
        rows = [r for r in rows if _key(r) not in done]

    # Findability first, fit second. Every row costs the same in web searches,
    # but they are not equally likely to yield anything: a rating agency
    # publishes its analysts' names and addresses outright, while a private
    # shop with no footprint burns the same eight searches and returns
    # UNKNOWN. Ordering by fit alone spent the budget on whichever role
    # matched the resume best, findable or not.
    #
    # `prefer` sits above both: when he wants a sector first (CRE this week),
    # every matching row outranks every non-matching one, and findability
    # only orders within each group.
    patterns = findability._load_patterns()
    tier_rank = {t: i for i, t in enumerate(findability.ORDER)}
    prefer_re = re.compile(prefer, re.I) if prefer else None

    def _wanted(row):
        if not prefer_re:
            return 0
        blob = " ".join(str(row.get(k) or "") for k in
                        ("Company", "Position", "Required Skills & Experience"))
        return 0 if prefer_re.search(blob) else 1

    def priority(row):
        tier, _ = findability.classify(row, patterns)
        return (_wanted(row), tier_rank.get(tier, 99),
                -batch_outreach.fit_score(row))

    ranked = sorted(rows, key=priority)

    # One company per run. Ranking by findability put three Fitch rows at
    # the top and a --limit 3 run researched all three, landing a fourth and
    # fifth cold email on a firm that already held two drafts and an active
    # warm thread. Colleagues compare notes; a fifth email from the same
    # graduate reads as a mail merge and undercuts the referral already in
    # motion there. Spread the budget across firms instead.
    from company_match import slug as company_slug
    targets, seen = [], set()
    for row in ranked:
        # Fit is how the budget gets split among rows he has no view on. A
        # row he asked for by sector is not one of those, and most of the
        # CRE rows score 1.0 only because the sheet has no skills text for
        # them -- a gap in the data, not a verdict on the role.
        if _wanted(row) and batch_outreach.fit_score(row) < min_fit:
            continue
        key = company_slug(row.get("Company", ""))
        if key in seen:
            continue
        seen.add(key)
        targets.append(row)
        if len(targets) >= limit:
            break

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
            # Keep what the model said anyway. A skip with nothing on disk
            # is undiagnosable -- whether it found the posting, how far it
            # got, what it tried -- and the same row will be retried next
            # run, so the reason it failed is exactly what needs reading.
            skipped_path = os.path.join(
                out_dir, f"{row['row_number']:03d}_"
                         f"{batch_outreach._slug(row['Company'])}.SKIPPED.txt")
            with open(skipped_path, "w", encoding="utf-8") as f:
                f.write(f"Company:  {row['Company']}  (sheet row {row['row_number']})\n"
                        f"Role:     {row['Position']}\n"
                        f"Job link: {fields.get('job_url', 'UNKNOWN')}\n"
                        f"Sources:\n{fields.get('sources', '(none)')}\n"
                        f"\n{'=' * 70}\nRAW RESPONSE\n{'=' * 70}\n"
                        f"{fields.get('_raw', '(empty)')}\n")
            print(f"    no named person found — skipped rather than invented "
                  f"(see {skipped_path})")
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
                # The research is the expensive part and it is already on
                # disk. A Gmail per-minute quota hit here -- one 403 -- used
                # to raise straight out of run(), killing the whole batch
                # after the first row and losing three rows that had not
                # even been researched yet. Back off and retry; if Gmail
                # still refuses, say so and keep going -- the .txt is there
                # to create the draft from by hand.
                for attempt in range(1, 6):
                    try:
                        gmail_client.create_draft(
                            to=c["addr"], subject=c["subject"],
                            body_text=c["body"], attachments=[resume])
                        print(f"    Gmail draft created (unsent) -> {c['addr']}")
                        break
                    except Exception as exc:
                        text = str(exc)
                        if "403" in text or "429" in text or "rateLimit" in text:
                            if attempt < 5:
                                time.sleep(20 * attempt)
                                continue
                        print(f"    ! Gmail draft not created for {c['addr']}: "
                              f"{text[:90]}\n      (research saved to {path})")
                        break

    _mark_done(done)
    print(f"\nDone. Drafts in {out_dir}/")
    print("Read the SOURCES line on each before sending — the hook is only "
          "worth\nanything if it is actually true.")
