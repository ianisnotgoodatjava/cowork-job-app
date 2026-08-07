"""Finds job applications in Gmail that aren't in the sheet yet and adds them.

Company/role are parsed from the confirmation email's sender and subject.
Rows that can't be parsed confidently are reported rather than guessed at —
a wrong row is worse than a missing one, since you'd have to find and fix it.
"""

import re
from email.utils import parsedate_to_datetime

import gmail_client
import sheets
from config import HEADERS
from company_match import normalize
from link_finder import CONFIRMATION_PHRASES

# Only patterns where the captured group is unambiguously a COMPANY.
# "interest in X" is deliberately excluded: X is a role as often as a company
# ("interest in Analyst, Fixed Income Pricing"), and a role written into the
# company column is worse than no row at all.
_SUBJECT_COMPANY_RES = [
    re.compile(r"\byour application to\s+.+?\s+at\s+(.+?)\s*$", re.I),
    re.compile(r"\bapplying to(?:\s+the)?\s+(.+?)(?:\s*[!.]|\s*$)", re.I),
    re.compile(r"\bapplication (?:to|with)\s+(.+?)(?:\s*[!.]|\s*$)", re.I),
]

# A captured "company" that is really a role/requisition, e.g.
# "Analyst, Fixed Income Pricing" or "RQ094547 - Trading Assistant".
_ROLE_LIKE_RE = re.compile(
    r"^(the\s+)?(analyst|associate|trader|intern|senior|junior|vice|director|"
    r"manager|specialist|assistant|underwrit|research|trading|portfolio|"
    r"credit|equity|fixed income|sales|quantitative|investment|finance|"
    r"commercial|rq\d|jr_|r\d{4,})",
    re.I,
)
_ROLE_TAIL_RE = re.compile(r"\s+[-–]\s+(analyst|associate|trader|senior|junior).*$", re.I)
# Trailing boilerplate/requisition noise left on a captured company name,
# e.g. "iCapital - Application Received", "BNY - 69959", "First Horizon - FHNF".
_COMPANY_TAIL_RE = re.compile(
    r"\s*[-–]\s*(application received|\d+|[A-Z]{3,}\b.*)$", re.I
)
# "Risk Analyst at Fanatics" — the real company follows the final " at ".
_TRAILING_AT_RE = re.compile(r"^.*\bat\s+(.+)$", re.I)
_SUBJECT_ROLE_RES = [
    re.compile(r"\byour application to\s+(.+?)\s+at\s+.+?$", re.I),
    re.compile(r"\bapplication for\s+(.+?)(?:\s*[-–,]\s*\d+)?\s*$", re.I),
    re.compile(r"\bapplying to(?: the)?\s+(.+?)\s+(?:role|position)\b", re.I),
]

_NOISE_SUBJECT_RE = re.compile(
    r"\b(job alert|is hiring|new job|job opening|talent community|"
    r"stay in touch|survey|newsletter|digest|webinar|verify your|"
    r"confirm your identity|sign-in link|saved as a draft|incomplete)\b",
    re.I,
)


# Words appended to a company's name in a sender's display name:
# "Bank of New York Mellon Careers", "Barings Talent Acquisition".
_SENDER_ROLE_TAIL_RE = re.compile(
    r"\s*[-|,]?\s*\b(careers?( (opportunities|center|site|portal|team))?|"
    r"recruiting( team)?|recruitment|recruiter|talent (acquisition|network)|"
    r"talent|hiring( team)?|hr( team)?|human resources|jobs?|workday|ats|"
    r"people team|no[- ]?reply|do[- ]?not[- ]?reply|notifications?|team|"
    r"applications?|candidate (care|experience|services|support)|"
    r"people (services|operations)|university relations|campus recruiting)\b\s*$",
    re.I,
)

# The same tokens, but run together with the name so there is no word boundary
# for the rule above to find: "WellsFargoHR", "MUFGUBHR", "HRISWorkday
# hyperiongrp", "MyHR", "HRIS Flagstar". These reached the sheet as companies.
_HR_SYSTEM_RE = re.compile(
    r"^(?:my)?(?:hris|hr)(?=[A-Z\s])\s*|"           # leading: MyHR, HRIS Flagstar
    r"(?:hris?|hr|ukg|adp)$|"                       # trailing: WellsFargoHR
    r"(?:workday|successfactors|taleo|icims|ukg)",  # anywhere: HRISWorkday
    re.I,
)


def _strip_hr_system(name: str) -> str:
    """Removes HR-platform noise glued to a company name.

    Returns "" both when nothing is left ("MyHR" is all noise) and when the
    remainder is too short to be a company, so the caller falls back to the
    sender's domain rather than logging the HR system as an employer.
    """
    stripped = _clean(_HR_SYSTEM_RE.sub(" ", name).replace("_", " "))
    stripped = re.sub(r"\s{2,}", " ", stripped).strip()
    return stripped if len(stripped) >= 3 else ""

# Display names that are the vendor, not the employer.
_SENDER_VENDOR_RE = re.compile(
    r"^(workday|myworkday|linkedin|indeed|ziprecruiter|glassdoor|monster|"
    r"nexxt|adzuna|jobtarget|icims|greenhouse|lever|taleo|smartrecruiters|"
    r"jobvite|brightmove|trinet|adp|paylocity|paycom|clearcompany|insperity|"
    r"jazzhr|workable|breezy|recruitee|hirebridge|ultipro|ceridian|dayforce|"
    r"rippling|applytojob|applyresponse|donotreply|apply|recruiting ny|"
    # Job boards and aggregators are not employers. "Searchfunder" was logged
    # as a company you applied to.
    r"searchfunder|wellfound|angellist|builtin|dice|simplyhired|snagajob|"
    r"jobcase|untapped|joinhandshake|handshake|hiringcafe|otta|"
    r"welcometothejungle|efinancialcareers|wallstreetoasis|jobright|teal|"
    r"getro|ripplematch|"
    # No-reply variants. "Do Not Respond" reached the sheet as a company.
    r"do.?not.?respond|donotrespond|noresponse|unattended|automated|"
    r"auto.?reply|mailbot|mailer|"
    r"system generated|mail delivery|postmaster|no.?reply|notification)",
    re.I,
)

# Words that are never a company on their own. These came through as real
# rows: a sender called "bp Global" left "Global" once the org token logic
# ran, and "The" survived a subject capture.
_NOT_A_COMPANY = {
    "global", "the", "team", "careers", "career", "recruiting", "talent",
    "hiring", "jobs", "job", "company", "group", "online", "sender",
    "sendercompany", "notification", "notifications", "apply", "application",
}

# Tokens that mark a display name as an organisation rather than a person.
_ORG_TOKEN_RE = re.compile(
    r"\b(group|capital|bank|banking|llc|l\.l\.c|inc|incorporated|corp|"
    r"corporation|partners|advisors?|advisory|management|securities|trust|"
    r"holdings?|investments?|research|financial|finance|technologies|systems|"
    r"solutions|energy|infrastructure|asset|markets|global|international|"
    r"associates|ventures|equity|insurance|services|company|co\.|university|"
    r"institute|fund|funds|analytics|labs|health|media|properties|realty)\b",
    re.I,
)

# "Melissa Rupp [DRIEHAUS CAPITAL MANAGEMENT]" — the employer is bracketed.
_BRACKETED_ORG_RE = re.compile(r"[\[(]([^\])]{3,60})[\])]")


_WORD = r"[A-Z][a-z'’\-]+\.?"
# "Zhang, Calvin" — Outlook's Last, First form, which the plain word test
# missed because of the comma, so a recruiter became a company.
_INVERTED_NAME_RE = re.compile(rf"^{_WORD},\s*{_WORD}(?:\s+[A-Z]\.?)?$")
_PLAIN_NAME_RE = re.compile(rf"^{_WORD}(?:\s+[A-Z]\.?)?\s+{_WORD}(?:\s+{_WORD})?$")


def _looks_like_person(name: str) -> bool:
    """A recruiter's name rather than an employer.

    Their name is not the company, and writing it into the Company column
    produced rows like "Eunice Wang", "Katie Gruber" and "Zhang, Calvin".
    """
    name = name.strip()
    if _ORG_TOKEN_RE.search(name):
        return False
    return bool(_INVERTED_NAME_RE.match(name) or _PLAIN_NAME_RE.match(name))


def _company_from_sender(from_header: str) -> str:
    """Company taken from the sender, when the subject gives nothing.

    Most unparsed confirmations are generic ("Thank you for applying!") but
    arrive from "Ramirez Asset Management Careers <...>". The display name was
    being thrown away, which is why ~350 emails were skipped.
    """
    header = (from_header or "").strip()
    display = header.split("<")[0].strip().strip('"').strip()

    if display and not _SENDER_VENDOR_RE.match(display):
        # A recruiter often signs with their employer in brackets.
        bracketed = _BRACKETED_ORG_RE.search(display)
        if bracketed:
            display = bracketed.group(1)
        else:
            display = _BRACKETED_ORG_RE.sub("", display)

        name = _clean(_SENDER_ROLE_TAIL_RE.sub("", display))
        # Strip a repeated tail ("Acme Careers Team", "Acme Workday").
        for _ in range(2):
            name = _clean(_SENDER_ROLE_TAIL_RE.sub("", name))
        # Then the run-together forms the word-boundary rule cannot see. When
        # the name is nothing but HR-system noise ("MyHR") it is cleared, so
        # the domain below decides instead of "MyHR" becoming an employer.
        if _HR_SYSTEM_RE.search(name):
            name = _strip_hr_system(name)

        # Nobody signs a personal email "Neuberger Berman Workday", so a
        # stripped suffix proves this is an employer. Without that proof, a
        # two-word capitalised name is ambiguous — "Goldman Sachs" and
        # "Eunice Wang" look identical — so fall back to the domain, which
        # settles it.
        suffix_stripped = name.lower() != display.lower()
        domain_label = ""
        if "@" in header:
            from company_match import _domain_label
            domain_label = _domain_label(header)
        flat = re.sub(r"[^a-z0-9]", "", name.lower())
        initials = "".join(w[0] for w in name.split() if w).lower()
        corroborated = bool(domain_label) and (
            flat[:8] in domain_label
            or domain_label in flat
            # "Goldman Sachs" <careers@gs.com> — finance abbreviates to
            # initials, and the person-name test cannot tell that pair from
            # "Eunice Wang".
            or (len(initials) >= 2 and domain_label == initials)
        )

        if 2 < len(name) <= 60 and not _ROLE_LIKE_RE.match(name) \
                and "@" not in name and not re.match(r"^\d", name) \
                and (suffix_stripped or corroborated
                     or not _looks_like_person(name)):
            return name

    from company_match import _domain_label, _is_vendor_domain
    from calendar_client import _GENERIC_DOMAINS
    if "@" in header:
        domain = header.rsplit("@", 1)[1].lower().strip(" >")
        # A recruiter mailing from their own gmail tells us nothing; without
        # this, "Evan Monterroso <e@gmail.com>" logged a company called Gmail.
        if not _is_vendor_domain(domain) and domain not in _GENERIC_DOMAINS:
            label = _domain_label(header)
            # The domain-marker list covers the big ATSes; this catches the
            # long tail (paycomonline.net, clearcompany.com) by name.
            if label and len(label) >= 4 and not _SENDER_VENDOR_RE.match(label):
                return label
    return ""


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text.strip(" -–—,:;!.")


def _parse(msg: dict) -> tuple[str, str] | None:
    subject = msg.get("subject", "")
    if _NOISE_SUBJECT_RE.search(subject):
        return None

    company = role = ""
    for rx in _SUBJECT_COMPANY_RES:
        m = rx.search(subject)
        if m:
            company = _clean(m.group(1))
            break
    for rx in _SUBJECT_ROLE_RES:
        m = rx.search(subject)
        if m:
            role = _clean(m.group(1))
            break

    # Subjects are usually generic ("Thank you for applying!"); the body names
    # the role ("...for the position of Business Analyst"). Reading only the
    # subject is what put the "Analyst" placeholder on 126 rows.
    if not role and msg.get("body"):
        import role_extract
        role = role_extract.extract(subject, msg["body"], company)[0]

    # Trim "Analyst at Acme" / "Analyst - Acme" down to "Analyst". Without
    # this the same application logged twice under two slightly different
    # titles hashes to two keys and dedupe never fires.
    if role:
        from fix_positions import tidy_role
        import role_extract
        # Same clean-up the body extractor applies, so a title taken from the
        # subject is not left carrying "position of ..." or a requisition
        # number that the body-derived ones have stripped.
        role = role_extract._tidy(tidy_role(role), company)

    company = _clean(_COMPANY_TAIL_RE.sub("", _ROLE_TAIL_RE.sub("", company)))
    # A role-shaped capture may still carry the real company after " at ".
    if _ROLE_LIKE_RE.match(company):
        m = _TRAILING_AT_RE.match(company)
        company = _clean(m.group(1)) if m else ""
        if _ROLE_LIKE_RE.match(company):
            company = ""

    # Subject gave nothing usable — fall back to who sent it.
    if not company:
        company = _company_from_sender(msg.get("from", ""))
    # Requisition/job-ID fragments ("10073106-WD Credit...", "2025") are never
    # company names.
    if re.match(r"^\d", company):
        return None
    if not company or len(company) < 3 or len(company) > 60:
        return None
    if company.lower().strip() in _NOT_A_COMPANY:
        return None
    return company, role


def scan(days_back: int = 730, dry_run: bool = False,
         max_results: int = 4000) -> None:
    phrase_query = " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES)
    # Not `in:inbox`: confirmations get archived, auto-filtered into labels or
    # swept into tabs, and those applications still happened. The same
    # restriction was removed from rejection_scanner for the same reason.
    query = f"newer_than:{days_back}d ({phrase_query}) -from:nyu.edu"
    print(f"Searching Gmail: {query}\n")

    # Bodies cost an extra round trip per message, but they are where the job
    # title actually is — without them every generic confirmation lands in the
    # sheet as "Analyst".
    msgs = gmail_client.search_messages(query, max_results=max_results,
                                        include_body=True)
    print(f"Found {len(msgs)} candidate confirmation emails.")

    existing = sheets.list_rows()
    # Keyed on the distinctive core, not the full name: the sheet says "BMO
    # Capital Markets" while the confirmation says "BMO", and keying on the
    # full name let that through as a second row for the same application.
    from company_match import _core as _company_core

    def _key(company: str, role: str) -> tuple[str, str]:
        return (_company_core(company).replace(" ", "") or normalize(company),
                normalize(role))

    existing_keys = {_key(r["Company"], r["Position"]) for r in existing}
    # Compared on the distinctive core, so "bp Global" collides with the "bp"
    # row you already have, and "Fitch Group" with "Fitch" — the sender's
    # display name spells companies differently from the sheet.
    from company_match import _core
    # Space-stripped too: a company taken from a domain label has no spaces
    # ("chimerasecurities"), so it never matched the sheet's "Chimera
    # Securities" and re-imported as a duplicate row.
    existing_companies = set()
    for r in existing:
        if not r["Company"].strip():
            continue
        for c in (normalize(r["Company"]), _core(r["Company"])):
            if c:
                existing_companies.add(c)
                existing_companies.add(c.replace(" ", ""))

    new_rows = {}
    unparsed = []
    for m in msgs:
        parsed = _parse(m)
        if not parsed:
            unparsed.append(m)
            continue
        company, role = parsed
        # Built with _key(), so it must be compared with _key(). Keying the
        # incoming side on normalize() instead meant "MasterCard People
        # Services" never collided with the "MasterCard" row already in the
        # sheet, and the same application was logged twice.
        key = _key(company, role)
        if key in existing_keys or key in new_rows:
            continue
        # With no role parsed there's nothing to distinguish this from an
        # application already tracked at the same company, so skip it rather
        # than create a likely duplicate under a placeholder role.
        if not role and any(
            form in existing_companies
            for form in (normalize(company), _core(company),
                         normalize(company).replace(" ", ""),
                         _core(company).replace(" ", ""))
        ):
            continue
        try:
            date = parsedate_to_datetime(m["date"]).date()
        except Exception:
            continue
        new_rows[key] = {
            "company": company,
            "role": role or "Analyst",
            "date": f"{date.month}/{date.day}/{date.year}",
            "subject": m["subject"],
            "known_company": normalize(company) in existing_companies,
        }

    print(f"\n{len(new_rows)} application(s) not yet in the sheet:")
    for v in sorted(new_rows.values(), key=lambda x: x["date"]):
        flag = "" if v["known_company"] else "  [new company]"
        print(f"  {v['date']:10s} | {v['company'][:35]:35s} | {v['role'][:40]}{flag}")

    if unparsed:
        print(f"\n{len(unparsed)} email(s) couldn't be parsed into company/role (skipped):")
        for m in unparsed[:15]:
            print(f"  \"{m['subject'][:80]}\"")
        if len(unparsed) > 15:
            print(f"  ... and {len(unparsed) - 15} more")

    if dry_run:
        print("\n(dry-run — nothing written)")
        return
    if not new_rows:
        return

    sheets.ensure_headers()
    values = []
    for v in new_rows.values():
        row = {
            "Company": v["company"],
            "Position": v["role"],
            "Applied Draes": v["date"],
            "Status": "Applied",
            "Notes": "Auto-added from Gmail confirmation email",
        }
        values.append([row.get(h, "") for h in HEADERS])

    # One batched multi-row append (not sheets.append_job_row per row, which
    # would cost one round trip per application instead of one for all of them).
    svc = sheets._service()
    result = svc.spreadsheets().values().append(
        spreadsheetId=sheets.SHEET_ID,
        range=f"{sheets.SHEET_TAB}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()
    print(f"\nAdded {result['updates']['updatedRows']} row(s) to the sheet.")
