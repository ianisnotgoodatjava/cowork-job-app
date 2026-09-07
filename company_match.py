"""Shared company-name matching for the Gmail scanners.

Uses word-boundary regex, not plain substring containment — a company like
"ING" normalizes to "ing", which is a substring of ordinary words like
"applying" or "interesting". Plain `"ing" in text` produced false-positive
matches on nearly every email. Word boundaries plus a minimum length guard
against that.
"""

import re
from functools import lru_cache

_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|group|holdings|partners|lp|llp)\.?\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=None)
def normalize(name: str) -> str:
    name = _SUFFIX_RE.sub("", name)
    name = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


# Trailing words that describe what kind of firm it is, not which firm. The
# sheet says "Flagstar Bank" while the recruiter writes "Flagstar Analyst
# Position", so the full name never appears and the row went unmatched.
_DESCRIPTOR_RE = re.compile(
    r"\s+\b(bank|research|securities|capital|financial|finance|markets|"
    r"investments?|investment management|asset management|management|advisors?|"
    r"advisory|associates|technologies|solutions|services|international|global|"
    r"america|americas|usa|us|na)\b\s*$",
    re.IGNORECASE,
)


@lru_cache(maxsize=None)
def _core(company: str) -> str:
    """The distinctive part of a name: 'Flagstar Bank' -> 'flagstar'."""
    name = normalize(company)
    while True:
        stripped = _DESCRIPTOR_RE.sub("", name).strip()
        if stripped == name:
            return name
        name = stripped


def _mentions(needle: str, haystack: str) -> bool:
    pattern = r"\b" + re.escape(needle).replace(r"\ ", r"\s+") + r"\b"
    return re.search(pattern, haystack) is not None


def company_mentioned(company: str, haystack: str) -> bool:
    normalized = normalize(company)
    # Word boundaries (below) already stop "ING" matching inside "applying",
    # so a length floor is unnecessary and blocked real short names like CME,
    # UBS, BNY and NRG from ever matching.
    if len(normalized) < 2:
        return False
    # Strict, full-name only. Matching on the shortened core was tried and
    # reverted: "Valuation Research Corp" collapses to "valuation", which
    # appears in any accounting firm's mail and wrongly closed a live row.
    # The shortened form is only trusted against a sender's domain, where it
    # cannot collide with prose — see company_in_sender().
    return _mentions(normalized, haystack)


# Applicant-tracking and mail vendors. Every company's mail flows through
# these, so a name matching one of them means nothing.
_VENDOR_DOMAINS = {
    "myworkday", "workday", "oraclecloud", "taleo", "icims", "greenhouse",
    "greenhouse-mail", "lever", "hire", "successfactors", "sap", "brassring",
    "kenexa", "jobvite", "smartrecruiters", "ashbyhq", "bamboohr", "paylocity",
    "adp", "ultipro", "dayforce", "phenompeople", "avature", "eightfold",
    "ripplematch", "handshake", "symplicity", "linkedin", "indeed",
    "ziprecruiter", "glassdoor", "monster", "nexxt", "adzuna", "trinethire",
    "brightmove", "gmail", "googlemail", "outlook", "hotmail", "yahoo",
    "sendgrid", "mailchimp", "salesforce", "notifications",
}


# Matched against the whole domain, not just its label. BNY's careers mail
# comes from workflow.mail.us2.cloud.oracle.com, whose registrable domain
# really is oracle.com — so the label test alone marked BNY's rejections as
# Oracle's. Any company hosting on someone else's recruiting infrastructure
# hits this. The cost is that Oracle's own mail no longer matches by sender;
# it still matches by name in the subject, which is where it always appeared.
_VENDOR_DOMAIN_MARKERS = (
    "oracle.com", "oraclecloud", "myworkday", "workday", "icims", "taleo",
    "greenhouse", "lever.co", "successfactors", "brassring", "kenexa",
    "jobvite", "smartrecruiters", "ashbyhq", "avature", "phenompeople",
    "eightfold", "bamboohr", "paylocity", "ultipro", "dayforce", "adp.com",
    "workflow.mail", "brightmove", "trinethire", "ripplematch", "handshake",
    "symplicity", "sendgrid", "mailchimp", "amazonses",
)


def _is_vendor_domain(domain: str) -> bool:
    return any(marker in domain for marker in _VENDOR_DOMAIN_MARKERS)


def _domain_label(from_header: str) -> str:
    """The registrable label of the sender's domain: a@mail.flagstar.com -> flagstar."""
    domain = from_header.rsplit("@", 1)[1].lower().strip(" >")
    parts = [p for p in re.sub(r"[^a-z0-9.]", "", domain).split(".") if p]
    if len(parts) < 2:
        return ""
    # Handles co.uk / com.au style suffixes, and vendor-ish thirds like
    # miraeasset.us.com — which resolved to "us" and made every Mirae Asset
    # message look like it came from no company at all.
    if len(parts) >= 3 and (parts[-2] in {"co", "com", "org", "net", "ac", "gov"}
                            or len(parts[-2]) == 2):
        return parts[-3]
    return parts[-2]


def company_in_sender(company: str, from_header: str) -> bool:
    """True when the sender writes from this company's own domain.

    The domain label must equal the company's name (or its name minus a
    descriptor) exactly. Substring matching was tried and reverted: it let
    "Oracle" match every company whose ATS runs on oraclecloud.com, silently
    marking unrelated rows rejected.
    """
    if "@" not in (from_header or ""):
        return False
    domain = from_header.rsplit("@", 1)[1].lower().strip(" >")
    if _is_vendor_domain(domain):
        # An ATS tenant puts the employer in the local part instead:
        # usbank@myworkday.com, conning@myworkday.com, baird@myworkday.com.
        # Returning False here meant three U.S. Bank rejections — for roles
        # applied to by requisition number, so the subject never spelled the
        # company either — matched no row at all, and outreach was drafted to
        # three desks that had already declined.
        local = re.sub(r"[^a-z0-9]", "", from_header.rsplit("@", 1)[0].lower())
        if len(local) < 4:
            return False
        return local in _sender_names(company)
    label = _domain_label(from_header)
    if not label or len(label) < 4 or label in _VENDOR_DOMAINS:
        return False
    return label in _sender_names(company)


def _sender_names(company: str) -> set[str]:
    """The forms a sender's domain label or ATS local-part may take.

    The whole name with only punctuation removed has to be in here. Both
    normalize() and _core() strip "Group" as a legal suffix, so "CME Group"
    reduced to "cme" — three characters, below the length floor, leaving
    nothing at all to match cmegroup.com with.
    """
    names = {
        re.sub(r"[^a-z0-9]", "", (company or "").lower()),
        normalize(company).replace(" ", ""),
        _core(company).replace(" ", ""),
    }
    return {n for n in names if len(n) >= 4}


def slug(company: str) -> str:
    """Company name reduced to a comparison key: "Banc of California" ->
    "bancofcalifornia".

    Was copied into findability, draft_cleanup and targets, byte for byte in
    two of them. Three copies of a matching rule is how they drift, and this
    one decides whether a draft gets deleted -- so it lives next to _core and
    normalize, which it is built from.
    """
    return (_core(company or "").replace(" ", "")
            or normalize(company or "").replace(" ", ""))
