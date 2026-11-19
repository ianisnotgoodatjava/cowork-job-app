"""Reads a job-application confirmation email into a company and a role.

Pure. A message here is a dict of headers and body text, and nothing in this
module opens a socket. That is the whole point of it existing. These rules
used to live inside `sources/app_scanner`, welded to the sheet reconciliation
that runs after them, so the only way to exercise a change to any of the
forty-odd patterns below was to run the live nightly scan against a real
mailbox and read what it printed. Every one of them was written blind.

Two things changed besides the move.

**A refusal now says why.** The old `_parse` returned `None` both for "this is
a job alert, it was never an application" and for "this is an application and
I could not read it". So the nightly log printed one undifferentiated list of
skipped subjects, and the list never got shorter, because there was no way to
tell which entries were bugs and which were the parser working correctly.
Every refusal now carries a reason from `REASONS`, and `measure()` counts
them, which turns "27% unparsed" into buckets you can attack one at a time.

**A sender-specific parser may use context the generic rules cannot.** The
generic rules deliberately refuse to read "interest in X" as a company,
because X is a role about as often ("...your interest in Analyst, Fixed
Income Pricing"), and a job title written into the Company column is worse
than no row at all — you have to find it and fix it by hand. But when the
sender is `"Workday at S&P Global" <spgi@myworkday.com>`, the employer is
already known from the display name, so "interest in X" can only be the role.
Workday was the worst source in this mailbox, missing 56% of its
confirmations, precisely because it leaves the subject line to each employer
and puts the role in the body. Knowing who sent it is what makes those
readable.
"""

import re
from dataclasses import dataclass

from core import role_extract
from core.job_title import tidy_role
from core.company_match import (
    GENERIC_DOMAINS, _domain_label, _is_vendor_domain,
    company_mentioned, normalize,
)


# --------------------------------------------------------------- outcomes

@dataclass(frozen=True)
class Parsed:
    """A confirmation that was read. `parser` names the rule that read it."""

    company: str
    role: str
    parser: str = "generic"

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True)
class Skipped:
    """A confirmation that produced no row, and the reason it produced none.

    Falsy, so `if not parse(msg): continue` reads exactly as it did when this
    was a `None`.
    """

    reason: str
    detail: str = ""

    def __bool__(self) -> bool:
        return False


# Every reason `parse` can give, and what it means about the mailbox. The
# scan prints these as counts. A bad week then shows up as one bucket
# growing, instead of as a longer list of subject lines that nobody reads.
REASONS = {
    "noise": "a job alert, digest or newsletter — never an application",
    "not-an-application": "an account or talent-community signup",
    "no-company": "neither the subject, the display name nor the domain named an employer",
    "role-as-company": "the only candidate name was a job title",
    "company-too-short": "the candidate name was under 3 characters",
    "company-too-long": "the candidate name ran past 60 characters",
    "company-numeric": "the candidate name began with a digit — a requisition id, not a firm",
    "company-blocklisted": "the candidate name is a word that is never an employer on its own",
}


# Only patterns where the captured group is unambiguously a COMPANY.
# "interest in X" is deliberately excluded: X is a role as often as a company
# ("interest in Analyst, Fixed Income Pricing"), and a role written into the
# company column is worse than no row at all.
_SUBJECT_COMPANY_RES = [
    # LinkedIn's own Easy Apply receipt ("Ian, your application was sent to
    # Adore Me") — distinct from an employer's ATS confirmation, and the
    # subject never carries a role, only the company. Role falls through to
    # the body-reading extractor below, same as any other subject that
    # doesn't name it.
    re.compile(r"\byour application was sent to\s+(.+?)\s*$", re.I),
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
# The all-caps branch must stay case-sensitive despite the re.I on the rest
# of the pattern (needed so "application received" matches regardless of
# case) -- with re.I applied to it too, [A-Z]{3,} matched any 3+ letter word
# case-insensitively, not just real all-caps codes, and silently truncated
# "Madison-Davis, LLC" down to "Madison".
_COMPANY_TAIL_RE = re.compile(
    r"\s*[-–]\s*(application received|\d+|(?-i:[A-Z]{3,})\b.*)$", re.I
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


# What a stripped name should have been. Removing the HR suffix is not always
# enough: "MUFGUBHR" loses the "HR" and leaves "MUFGUB", because "UB" is Union
# Bank — part of the old MUFG Union Bank name, not platform noise. Keyed on the
# lowercased result of the strip.
_CANONICAL = {
    "mufgub": "MUFG",
    "mufg union bank": "MUFG",
}


def _canonicalise(name: str) -> str:
    return _CANONICAL.get(name.strip().lower(), name)


def _strip_hr_system(name: str) -> str:
    """Removes HR-platform noise glued to a company name.

    Returns "" both when nothing is left ("MyHR" is all noise) and when the
    remainder is too short to be a company, so the caller falls back to the
    sender's domain rather than logging the HR system as an employer.
    """
    stripped = _clean(_HR_SYSTEM_RE.sub(" ", name).replace("_", " "))
    stripped = re.sub(r"\s{2,}", " ", stripped).strip()
    return _canonicalise(stripped) if len(stripped) >= 3 else ""

# Display names that are the vendor, not the employer.
_SENDER_VENDOR_RE = re.compile(
    r"^(workday|myworkday|linkedin|indeed|ziprecruiter|glassdoor|monster|"
    r"nexxt|adzuna|jobtarget|icims|greenhouse|lever|taleo|smartrecruiters|"
    r"jobvite|brightmove|trinet|adp|paylocity|paycom|clearcompany|insperity|"
    r"jazzhr|workable|breezy|recruitee|hirebridge|ultipro|ceridian|dayforce|"
    # saashr.com is the UKG Ready host. It reached the sheet as the
    # employer "saashr" for a SIFMA application.
    r"saashr|kronos|ukgready|myworkdayjobs|"
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

# "Jane Doe [DRIEHAUS CAPITAL MANAGEMENT]" — the employer is bracketed.
_BRACKETED_ORG_RE = re.compile(r"[\[(]([^\])]{3,60})[\])]")


_WORD = r"[A-Z][a-z'’\-]+\.?"
# "Doe, John" — Outlook's Last, First form, which the plain word test
# missed because of the comma, so a recruiter became a company.
_INVERTED_NAME_RE = re.compile(rf"^{_WORD},\s*{_WORD}(?:\s+[A-Z]\.?)?$")
_PLAIN_NAME_RE = re.compile(rf"^{_WORD}(?:\s+[A-Z]\.?)?\s+{_WORD}(?:\s+{_WORD})?$")


def _looks_like_person(name: str) -> bool:
    """A recruiter's name rather than an employer.

    Their name is not the company, and writing it into the Company column
    produced rows like "Jane Roe", "Mary Major" and "Doe, John".
    """
    name = name.strip()
    if _ORG_TOKEN_RE.search(name):
        return False
    return bool(_INVERTED_NAME_RE.match(name) or _PLAIN_NAME_RE.match(name))


# "<Employer> @ <ATS platform>" in a sender's display name.
_SENDER_PLATFORM_TAIL_RE = re.compile(
    r"\s*@\s*(icims|workday|myworkday|greenhouse|lever|taleo|ukg|ukgready|"
    r"successfactors|smartrecruiters|jobvite|rippling|ashby|workable)\s*$",
    re.I,
)


def _company_from_sender(from_header: str) -> str:
    """Company taken from the sender, when the subject gives nothing.

    Most unparsed confirmations are generic ("Thank you for applying!") but
    arrive from "Ramirez Asset Management Careers <...>". The display name was
    being thrown away, which is why ~350 emails were skipped.
    """
    header = (from_header or "").strip()
    display = header.split("<")[0].strip().strip('"').strip()
    # iCIMS sends as "Rockefeller Capital Management  @ icims" — employer
    # then platform. This has to come off before anything else: the
    # HR-system rule below removes "icims" on its own but leaves a dangling
    # "@", and the final guard rejects any name containing one. The whole
    # application was dropped on that stray character.
    display = _SENDER_PLATFORM_TAIL_RE.sub("", display).strip()

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
        # Twice, because one pass only strips what is trailing at the time.
        # "Nomura HR SuccessFactors" loses "SuccessFactors" first, which is
        # what the $-anchored branch was looking at; only then is "HR" at the
        # end for it to remove. A single pass left the employer as "Nomura
        # HR", which matches no sheet row and reads as a company that does
        # not exist.
        for _ in range(2):
            if not _HR_SYSTEM_RE.search(name):
                break
            stripped = _strip_hr_system(name)
            if stripped == name:
                break
            name = stripped

        # Nobody signs a personal email "Neuberger Berman Workday", so a
        # stripped suffix proves this is an employer. Without that proof, a
        # two-word capitalised name is ambiguous — "Goldman Sachs" and
        # "Jane Roe" look identical — so fall back to the domain, which
        # settles it.
        suffix_stripped = name.lower() != display.lower()
        domain_label = ""
        if "@" in header:
            domain_label = _domain_label(header)
        flat = re.sub(r"[^a-z0-9]", "", name.lower())
        initials = "".join(w[0] for w in name.split() if w).lower()
        corroborated = bool(domain_label) and (
            flat[:8] in domain_label
            or domain_label in flat
            # "Goldman Sachs" <careers@gs.com> — finance abbreviates to
            # initials, and the person-name test cannot tell that pair from
            # "Jane Roe".
            or (len(initials) >= 2 and domain_label == initials)
        )

        if 2 < len(name) <= 60 and not _ROLE_LIKE_RE.match(name) \
                and "@" not in name and not re.match(r"^\d", name) \
                and (suffix_stripped or corroborated
                     or not _looks_like_person(name)):
            return name

    if "@" in header:
        domain = header.rsplit("@", 1)[1].lower().strip(" >")
        # A recruiter mailing from their own gmail tells us nothing; without
        # this, "Jane Doe <jdoe@gmail.com>" logged a company called Gmail.
        if not _is_vendor_domain(domain) and domain not in GENERIC_DOMAINS:
            label = _domain_label(header)
            # The domain-marker list covers the big ATSes; this catches the
            # long tail (paycomonline.net, clearcompany.com) by name.
            if label and len(label) >= 4 and not _SENDER_VENDOR_RE.match(label):
                return label
    return ""


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text.strip(" -–—,:;!.")


# application. Phillips 66's "Welcome! Thank you for Creating an Account"
# closes with "thank you for your interest in Phillips 66" and went onto the
# sheet as an application under the ATS's own sender name, "ATS_P66"; a talent
# community signup does the same. Matched on the subject only: real ATS
# confirmations often invite you to create an account further down the body.
_NOT_AN_APPLICATION_RE = re.compile(
    r"creat(?:e|ed|ing)\s+(?:an?\s+|your\s+)?(?:account|profile)|"
    r"account\s+(?:has\s+been\s+|was\s+)?created|"
    r"talent\s+(?:community|network|pool)", re.I)


def _not_an_application(msg: dict) -> bool:
    return bool(_NOT_AN_APPLICATION_RE.search(msg.get("subject", "")))

# ------------------------------------------------------- the sender's parts

@dataclass(frozen=True)
class Sender:
    """The From header, split once so the per-ATS rules can read it cheaply."""

    display: str   # "Workday at S&P Global"
    local: str     # "spgi"
    domain: str    # "myworkday.com"
    header: str    # the whole thing, for the generic fallback

    @property
    def label(self) -> str:
        """The registrable label of the domain: mail.flagstar.com -> flagstar."""
        return _domain_label(self.header) if "@" in self.header else ""

    @property
    def is_personal(self) -> bool:
        return self.domain in GENERIC_DOMAINS

    @property
    def is_vendor(self) -> bool:
        return _is_vendor_domain(self.domain)


def _sender(from_header: str) -> Sender:
    header = (from_header or "").strip()
    display = header.split("<")[0].strip().strip('"').strip()
    local = domain = ""
    if "@" in header:
        addr = header.rsplit("<", 1)[-1].strip(" >")
        local, _, domain = addr.rpartition("@")
        local, domain = local.strip(), domain.lower().strip(" >")
    return Sender(display=display, local=local, domain=domain, header=header)


# ------------------------------------------------------------ shared rules

# "...your interest in Rating Analyst - Structured Finance". Only safe to read
# as a role once the employer is known from somewhere else, which is why it is
# here and not in _SUBJECT_ROLE_RES.
_INTEREST_IN_RE = re.compile(r"\binterest in\s+(.+?)\s*$", re.I)
# "Status change for your CLO Portfolio Analyst - ONSITE Carmel, IN
# application at CNO Financial Group" — Workday's status mails, and the only
# place the role appears.
_FOR_YOUR_X_APPLICATION_RE = re.compile(
    r"\bfor your\s+(.+?)\s+application\b", re.I)
# The user's own first name, which several ATSes append: "Thank you for your
# interest in CoBank, Ian".
_TRAILING_FIRST_NAME_RE = re.compile(r",\s*[A-Z][a-z]{1,14}\s*[!.]?\s*$")


def _role_knowing_company(subject: str, body: str, company: str) -> str:
    """The role, for a sender whose employer is already established.

    Tries the subject patterns the generic path uses, then the two that are
    only unambiguous once the company is known, then the body extractor.
    """
    for rx in _SUBJECT_ROLE_RES:
        m = rx.search(subject)
        if m:
            return _tidy_role_text(_clean(m.group(1)), company)

    cleaned = _TRAILING_FIRST_NAME_RE.sub("", subject)
    for rx in (_FOR_YOUR_X_APPLICATION_RE, _INTEREST_IN_RE):
        m = rx.search(cleaned)
        if not m:
            continue
        candidate = _clean(_drop_company_prefix(_clean(m.group(1)), company))
        # "interest in MUFG Bank, Ltd." names the employer again, not a role.
        # Comparing normalised forms, because the subject and the display name
        # spell the same firm differently.
        if candidate and normalize(candidate) != normalize(company):
            tidied = _tidy_role_text(candidate, company)
            if tidied:
                return tidied

    if body:
        return _tidy_role_text(
            role_extract.extract(subject, body, company)[0], company)
    return ""


# Workday subjects routinely name the employer, then a separator, then the
# requisition and role: "...interest in MUFG Bank, Ltd. : 10078709-WD
# Corporate Banking Analyst". Splitting on the first separator and checking
# whether the left side is the company is what tells those apart from a title
# that simply contains a dash ("Rating Analyst - Structured Finance").
_SUBJECT_SPLIT_RE = re.compile(r"\s*[:|–—]\s*|\s+-\s+")


def _drop_company_prefix(text: str, company: str) -> str:
    parts = _SUBJECT_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) == 2 and parts[1].strip() \
            and company_mentioned(company, parts[0]):
        return parts[1].strip()
    return text


def _tidy_role_text(role: str, company: str) -> str:
    """The clean-up every role gets, wherever it was read from.

    Without this a title taken from a subject keeps "position of ..." and its
    requisition number while a body-derived one has had both stripped, so the
    same application hashes to two keys and dedupe never fires.
    """
    if not role:
        return ""
    return role_extract._tidy(tidy_role(role), company)


# --------------------------------------------------------- the generic rules

def _generic(msg: dict, sender: Sender) -> Parsed | Skipped:
    """What to do with a confirmation whose sender nothing recognises.

    This is the original `app_scanner._parse`, moved intact. It reads the
    subject for a company, then for a role, then the body for a role, then
    falls back to the sender's display name and domain for the company.
    """
    subject = msg.get("subject", "")

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
        role = role_extract.extract(subject, msg["body"], company)[0]

    # Trim "Analyst at Acme" / "Analyst - Acme" down to "Analyst". Without
    # this the same application logged twice under two slightly different
    # titles hashes to two keys and dedupe never fires.
    role = _tidy_role_text(role, company)

    company = _clean(_COMPANY_TAIL_RE.sub("", _ROLE_TAIL_RE.sub("", company)))
    # A role-shaped capture may still carry the real company after " at ".
    if _ROLE_LIKE_RE.match(company):
        m = _TRAILING_AT_RE.match(company)
        company = _clean(m.group(1)) if m else ""
        if _ROLE_LIKE_RE.match(company):
            return Skipped("role-as-company", subject[:80])

    # Subject gave nothing usable — fall back to who sent it.
    if not company:
        company = _company_from_sender(sender.header)
    return _validated(company, role, "generic")


def _validated(company: str, role: str, parser: str) -> Parsed | Skipped:
    """The guards every parser's answer passes, with the reason it failed.

    These were a chain of bare `return None`s. Each one is a real row that
    reached the sheet once: a requisition id as an employer, "Global" left
    behind by the org-token rule, a 90-character subject fragment.
    """
    company = _clean(company or "")
    if not company:
        return Skipped("no-company")
    if re.match(r"^\d", company):
        return Skipped("company-numeric", company[:40])
    if len(company) < 3:
        return Skipped("company-too-short", company)
    if len(company) > 60:
        return Skipped("company-too-long", company[:40])
    if company.lower().strip() in _NOT_A_COMPANY:
        return Skipped("company-blocklisted", company)
    return Parsed(company=company, role=role or "", parser=parser)


# ------------------------------------------------------------- the registry

# Checked in order; the first whose domain marker appears in the sender's
# domain gets the message. A parser may return Skipped("no-company") to mean
# "I recognise this sender and still found nothing", or None to hand the
# message to the generic rules unchanged.
REGISTRY: list[tuple[str, tuple[str, ...], object]] = []


def parser(name: str, *domains: str):
    """Registers a per-ATS parser. Order of definition is order of trial."""

    def register(fn):
        REGISTRY.append((name, domains, fn))
        return fn

    return register


# "Workday at S&P Global" <spgi@myworkday.com> — the only reliable place the
# employer appears in a Workday mail, and the reason a Workday-specific rule
# is worth having at all.
_WORKDAY_AT_RE = re.compile(r"^workday\s+at\s+(.+?)\s*$", re.I)
# "...your CLO Portfolio Analyst ... application at CNO Financial Group".
_APPLICATION_AT_RE = re.compile(r"\bapplication at\s+(.+?)\s*$", re.I)


@parser("workday", "myworkday.com", "myworkdayjobs.com", "workday.com")
def _workday(msg: dict, sender: Sender):
    """Workday: the employer chooses the subject, so nothing is conventional.

    It was the worst source in this mailbox — 56% of its confirmations went
    unread — and the reason is structural rather than a missing pattern. A
    Workday subject is whatever the employer typed, and the role is usually in
    the body. What a generic rule cannot do, and this can, is establish the
    employer first: the display name is "Workday at <employer>", or the
    subject ends "...application at <employer>". Once the employer is known,
    "your interest in X" can only be the role, which is a reading the generic
    path has to refuse.

    Returns None when nothing here names an employer, so the message still
    reaches the generic rules rather than being refused outright.
    """
    subject = msg.get("subject", "")

    company = ""
    m = _WORKDAY_AT_RE.match(sender.display)
    if m:
        company = _clean(m.group(1))
    if not company:
        m = _APPLICATION_AT_RE.search(subject)
        if m:
            company = _clean(m.group(1))
    if not company:
        # "MUFGUBHR", "HRIS Flagstar", "No_Replies_CNO_Workday_Notification":
        # the display name with the platform noise taken off. This is the
        # shared rule, not a Workday one, but Workday is where most of those
        # malformed sender names come from.
        company = _company_from_sender(sender.header)
    if not company:
        return None

    return _validated(
        company, _role_knowing_company(subject, msg.get("body", ""), company),
        "workday")


def parse(msg: dict) -> Parsed | Skipped:
    """Reads one confirmation email. Never raises; always says why on failure."""
    subject = msg.get("subject", "")
    if _NOISE_SUBJECT_RE.search(subject):
        return Skipped("noise", subject[:80])
    if _not_an_application(msg):
        return Skipped("not-an-application", subject[:80])

    sender = _sender(msg.get("from", ""))
    for name, domains, fn in REGISTRY:
        if any(marker in sender.domain for marker in domains):
            out = fn(msg, sender)
            if out is not None:
                return out
            break
    return _generic(msg, sender)


def measure(messages: list[dict]) -> dict:
    """Counts what `parse` did across a corpus, by parser and by reason.

    This is the number the redesign is judged on, and it needs no network:
    `tests/fixtures/confirmations.json` is a real pull, frozen.
    """
    from collections import Counter
    read, reasons, parsers = [], Counter(), Counter()
    for m in messages:
        out = parse(m)
        if out:
            read.append((m, out))
            parsers[out.parser] += 1
        else:
            reasons[out.reason] += 1
    # Noise and signups are the parser working, not failing: they are mail
    # that records no application. Counting them as misses is what made the
    # old 27% look unfixable.
    genuine = sum(n for r, n in reasons.items()
                  if r not in ("noise", "not-an-application"))
    return {
        "total": len(messages),
        "read": len(read),
        "missed": genuine,
        "excluded": sum(n for r, n in reasons.items()
                        if r in ("noise", "not-an-application")),
        "by_reason": dict(reasons),
        "by_parser": dict(parsers),
        "results": read,
    }
