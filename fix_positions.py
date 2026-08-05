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
_ROLE_RES = [
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


def tidy_role(role: str) -> str:
    """Trims a captured job title down to just the title."""
    role = re.sub(r"\s+", " ", role or "").strip()
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
            if 4 <= len(role) <= 70 and not _BAD_ROLE_RE.match(role) \
                    and _TITLEISH_RE.search(role):
                return role
    return ""


def _is_import_placeholder(row: dict) -> bool:
    return (IMPORT_NOTE in (row.get("Notes") or "")
            and (row.get("Position") or "").strip().lower() == PLACEHOLDER)


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
                if sent and abs((sent - applied).days) > 3:
                    continue
            role = extract_role(m.get("subject", ""), m.get("body", ""))
            if role and role.lower() != PLACEHOLDER:
                best = role
                break
        if best:
            recovered.append((r, best))
            print(f"  row {r['row_number']:3d} {r['Company'][:24]:24s} -> {best[:50]}")

    print(f"\n{len(recovered)} title(s) recovered; "
          f"{len(orphans) - len(recovered)} still unknown "
          f"(left as 'Analyst' rather than guessed).")

    if dry_run:
        print("\n(dry-run — nothing written)")
        return

    for r, role in recovered:
        notes = (r.get("Notes", "") + "\nTitle recovered from the confirmation email.").strip()
        sheets.update_row(r["row_number"], {"Position": role, "Notes": notes})
    removed = sheets.delete_rows([r["row_number"] for r, _ in dupes])
    print(f"\nUpdated {len(recovered)} title(s), removed {removed} duplicate row(s).")
