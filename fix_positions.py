"""Repairs rows the Gmail import filled with the placeholder role "Analyst".

`app_scanner` writes "Analyst" whenever it cannot parse a real job title out
of a confirmation email's subject. That is honest but unhelpful, and it has
two consequences worth undoing:

  1. **Duplicates.** A placeholder row often sits beside a row you had
     already written properly ("Willis Towers Watson / Analyst" next to
     "Willis Towers Watson / Portfolio Management Analyst"). The keys differ,
     so the import's own dedupe never caught it.
  2. **Lost titles.** The real title is usually in the email *body*
     ("your application for the position of ..."), which the subject-only
     parser never read.

Your own rows are the reference, never the thing being edited: this only
touches rows the import created.
"""

import re
from email.utils import parsedate_to_datetime

import gmail_client
import sheets
from company_match import _core, company_in_sender, company_mentioned, normalize
from date_utils import parse_date
from link_finder import CONFIRMATION_PHRASES

PLACEHOLDER = "analyst"
IMPORT_NOTE = "Auto-added from Gmail"

# Ordered most-specific first. Each must capture the title alone.
#
# The first group are subject-line shapes. The originals were written for body
# prose ("your application for the position of ...") and matched almost no
# subject, which is where the title usually is:
#   "Your application to Risk Analyst at Hartree Partners"     (LinkedIn)
#   "Status Update: 2026 Public Fixed Income Analyst - Job ID"  (MetLife)
#   "Thank you for your interest in Commercial & CX Graduate Associate"
_ROLE_RES = [
    re.compile(r"\bapplication (?:to|for)\s+(?:the\s+)?([^,;\n]{4,70}?)\s+(?:at|with)\s+[A-Z0-9]", re.I),
    re.compile(r"^\s*status update:\s*([^,;\n]{4,70}?)\s*(?:[-–]\s*job\s*id|position with|\bat\b|$)", re.I),
    re.compile(r"\bthank you for (?:your )?(?:applying|interest)(?: in| to| for)?\s+(?:the\s+)?([^,;\n(]{4,70}?)\s*(?:\(|position|role|opening|$)", re.I),
    re.compile(r"\byour application for\s+(?:the\s+)?([^,;\n(]{4,70}?)\s*(?:\(|position|role|opening|$)", re.I),
    re.compile(r"\bposition of\s+(?:the\s+)?([^.,;\n(]{4,70})", re.I),
    re.compile(r"\b(?:job title|position|role)\s*:\s*([^.,;\n(]{4,70})", re.I),
    re.compile(r"\bapplication for (?:the\s+)?(?:position of\s+)?([^.,;\n(]{4,70}?)\s*(?:position|role|opening)?\b[.,;\n]", re.I),
    re.compile(r"\bapplied (?:for|to)(?: the)?\s+([^.,;\n(]{4,70}?)\s+(?:position|role|opening)\b", re.I),
    re.compile(r"\binterest in (?:the\s+)?([^.,;\n(]{4,70}?)\s+(?:position|role|opening)\b", re.I),
    re.compile(r"\bregarding (?:the\s+)?([^.,;\n(]{4,70}?)\s+(?:position|role|opening)\b", re.I),
]

# A capture that is really boilerplate or a company name, not a title.
_BAD_ROLE_RE = re.compile(
    r"^(you|your|our|we|this|that|it|the|a|an|is|was|has|have|and|with|at|in|"
    r"for|to|of|thank|thanks|dear|hello|hi)\b|^\W*$", re.I)
_TITLEISH_RE = re.compile(
    r"\b(analyst|associate|trader|intern|manager|specialist|assistant|"
    r"engineer|coordinator|consultant|advisor|banker|underwriter|strategist|"
    r"researcher|developer|accountant|auditor|controller|director|officer|"
    r"representative|generalist|partner|clerk|administrator|operations|"
    r"trading|research|sales|finance|financial|credit|risk|portfolio|"
    r"investment|equity|quantitative|treasury|programme?|program|rotation)\b",
    re.I)


# Everything a captured title tends to drag along with it. Getting this wrong
# is not cosmetic: "Internal Wholesaler / Investment Consultant" and
# "Internal Wholesaler / Investment Consultant at Natixis Investment Managers"
# hash to different keys, so the same application was logged twice.
_ROLE_TRAILER_RE = re.compile(
    r"\s+(?:at|with|for)\s+[A-Z0-9].*$"          # "... at Everest"
    r"|\s*[>|].*$"                                # quoted-reply markers
    r"|\s+and\s+(?:thank|for|we|to)\b.*$"         # run-on into body prose
    r"|\s*,\s*and\b.*$"
    r"|\s+(?:position|role|opening|vacancy|req(?:uisition)?)\b\.?\s*$",
    re.S,
)


# Junk that survives the capture and has to come off the front.
_ROLE_LEADER_RE = re.compile(
    r"^[\s*\-–—•>|]+"                              # "* Analyst"
    r"|^(?:[Rr]-?\d{3,}|\d{5,})\s*[-–—:]?\s*"     # "R-29010 - ", "r206 "
    r"|^(?:role|position|title|job)\s*:\s*",        # "role: Analyst"
    re.I,
)

# Where the title stops and the email's prose starts. Cutting at the noun that
# introduces the sentence is what separates "Revenue Economics Analyst" from
# "Revenue Economics Analyst position here" — the capture regexes only bound
# the match loosely, so this does the real trimming.
_ROLE_PROSE_RE = re.compile(
    r"\s+(?:position|role|opening|opportunity|job|vacancy|req(?:uisition)?)\b.*$"
    r"|\.\s+\S.*$"                                 # first sentence boundary
    r"|\s+(?:we|the|this|that|thank|thanks|your|you|it|our|if|please|as|at|"
    r"in|for|and|to|is|was|has|have)\s*$"            # truncated next sentence
    # Words that never appear inside a job title, so anything from here on is
    # the email talking: "... Category Analyst We have considered ...".
    r"|\s+(?:we|thank|thanks|please|unfortunately|congratulations|dear|"
    r"hello|hi|regards)\b.*$",
    re.I | re.S,
)


def tidy_role(role: str) -> str:
    """Trims a captured job title down to just the title."""
    role = re.sub(r"\s+", " ", role or "").strip()
    role = _ROLE_LEADER_RE.sub("", role).strip()
    previous = None
    while role != previous:
        previous = role
        role = _ROLE_PROSE_RE.sub("", role).strip(" -–—,:;.\"'")
    previous = None
    while role != previous:
        previous = role
        role = _ROLE_TRAILER_RE.sub("", role).strip(" -–—,:;.\"'")
    return role


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" -–—,:;!.\"'")


def extract_role(subject: str, body: str) -> str:
    """Best real job title from a confirmation email, or ''."""
    for haystack in (subject or "", _clean(body)[:3000]):
        for rx in _ROLE_RES:
            m = rx.search(haystack)
            if not m:
                continue
            role = tidy_role(_clean(m.group(1)))
            # Trim a trailing requisition id.
            role = re.sub(r"\s*[-–(]\s*(req|requisition|job|id)?\s*[\w-]*\d[\w-]*\)?\s*$",
                          "", role, flags=re.I).strip(" -–—,")
            # A capture that trims down to the placeholder itself is no
            # better than what is already in the sheet, so keep looking
            # rather than returning it and blocking a later, fuller match.
            if role.lower() == PLACEHOLDER:
                continue
            if 4 <= len(role) <= 70 and not _BAD_ROLE_RE.match(role) \
                    and _TITLEISH_RE.search(role):
                return role
    return ""


def _is_import_placeholder(row: dict) -> bool:
    """A row whose title is the import's placeholder rather than a real one.

    This used to also require the "Auto-added from Gmail" marker in Notes.
    The sheet was later trimmed to columns A-J, Notes went with it, and the
    test became permanently false — the command reported nothing to do while
    116 placeholder rows sat in the sheet. The title alone is the signal:
    nobody hand-types a role of exactly "Analyst".
    """
    return (row.get("Position") or "").strip().lower() == PLACEHOLDER


def run(dry_run: bool = True, days_back: int = 520) -> None:
    rows = sheets.list_rows()
    targets = [r for r in rows if _is_import_placeholder(r)]
    print(f"{len(targets)} imported row(s) still carrying the placeholder "
          f"role, out of {len(rows)}.\n")
    if not targets:
        return

    # --- 1. Placeholders that duplicate a row you wrote yourself -----------
    def company_key(row):
        return _core(row["Company"]).replace(" ", "") or normalize(row["Company"])

    real_by_company: dict[str, list[dict]] = {}
    for r in rows:
        if _is_import_placeholder(r):
            continue
        if (r.get("Position") or "").strip():
            real_by_company.setdefault(company_key(r), []).append(r)

    dupes, orphans = [], []
    for r in targets:
        siblings = real_by_company.get(company_key(r), [])
        near = []
        d1 = parse_date(r.get("Applied Draes", ""))
        for s in siblings:
            d2 = parse_date(s.get("Applied Draes", ""))
            # Same company within a few weeks is the same application
            # confirmed twice, not two separate applications.
            if d1 and d2 and abs((d1 - d2).days) <= 30:
                near.append(s)
        if near:
            dupes.append((r, near[0]))
        else:
            orphans.append(r)

    print(f"=== {len(dupes)} placeholder row(s) duplicate a row you already wrote ===")
    for r, s in dupes:
        print(f"  DROP row {r['row_number']:3d} {r['Company'][:24]:24s} "
              f"| {r['Applied Draes']:10s}")
        print(f"  KEEP row {s['row_number']:3d} {s['Company'][:24]:24s} "
              f"| {s['Applied Draes']:10s} | {s['Position'][:44]}")

    # --- 2. Recover the real title from the confirmation email -------------
    print(f"\n=== recovering titles for the other {len(orphans)} ===")
    query = f"newer_than:{days_back}d (" + \
            " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES) + ")"
    msgs = gmail_client.search_messages(query, include_body=True, max_results=1200)
    print(f"(read {len(msgs)} confirmation email(s))\n", flush=True)

    recovered = []
    for r in orphans:
        applied = parse_date(r.get("Applied Draes", ""))
        best = ""
        for m in msgs:
            hay = f"{m['subject']} {m.get('snippet', '')}".lower()
            if not (company_mentioned(r["Company"], hay)
                    or company_in_sender(r["Company"], m.get("from", ""))):
                continue
            if applied:
                try:
                    sent = parsedate_to_datetime(m["date"]).date()
                except Exception:
                    sent = None
                # Widened from 3 days: an ATS "status update" often lands a
                # week after the application, and it names the role.
                if sent and abs((sent - applied).days) > 10:
                    continue
            role = extract_role(m.get("subject", ""), m.get("body", ""))
            if role and role.lower() != PLACEHOLDER:
                best = role
                break
        if best:
            recovered.append((r, best))
            print(f"  row {r['row_number']:3d} {r['Company'][:24]:24s} -> {best[:50]}")

    # --- 3. Second pass: search that company's mail directly ---------------
    # The broad query above only reads mail containing a confirmation phrase.
    # Plenty of ATS mail names the role without ever using one, so anything
    # still unknown gets its own targeted search.
    done = {r["row_number"] for r, _ in recovered}
    still = [r for r in orphans if r["row_number"] not in done]
    print(f"\n=== second pass: searching mail for the remaining "
          f"{len(still)} company(ies) ===", flush=True)

    for r in still:
        term = _core(r["Company"]) or r["Company"]
        if len(term) < 3:
            continue
        applied = parse_date(r.get("Applied Draes", ""))
        try:
            msgs2 = gmail_client.search_messages(
                f'newer_than:{days_back}d -in:chats "{term}"',
                include_body=True, max_results=40)
        except Exception as exc:
            print(f"  row {r['row_number']:3d} ! {type(exc).__name__}")
            continue
        for m in msgs2:
            if applied:
                try:
                    sent = parsedate_to_datetime(m["date"]).date()
                except Exception:
                    sent = None
                if sent and abs((sent - applied).days) > 10:
                    continue
            role = extract_role(m.get("subject", ""), m.get("body", ""))
            if role and role.lower() != PLACEHOLDER:
                recovered.append((r, role))
                print(f"  row {r['row_number']:3d} {r['Company'][:24]:24s} -> "
                      f"{role[:50]}", flush=True)
                break

    print(f"\n{len(recovered)} title(s) recovered; "
          f"{len(orphans) - len(recovered)} still unknown "
          f"(left as 'Analyst' rather than guessed).")

    if dry_run:
        print("\n(dry-run — nothing written)")
        return

    # One batched write instead of one call per row. Notes is not passed:
    # the sheet was trimmed to A-J and no longer has that column.
    written = sheets.update_rows(
        {r["row_number"]: {"Position": role} for r, role in recovered})
    removed = sheets.delete_rows([r["row_number"] for r, _ in dupes])
    print(f"\nUpdated {len(recovered)} title(s) ({written} cell(s) in one "
          f"call), removed {removed} duplicate row(s).")
