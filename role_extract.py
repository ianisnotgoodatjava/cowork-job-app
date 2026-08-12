"""Candidate title extractor -- developed against the real confirmation bodies."""
import re, html

# Words that make a capture plausibly a job title.
TITLEISH = re.compile(
    r"\b(analyst|associate|trader|trading|intern|manager|specialist|assistant|"
    r"engineer|coordinator|consultant|advisor|adviser|banker|underwriter|"
    r"strategist|researcher|research|developer|accountant|auditor|controller|"
    r"director|officer|representative|generalist|partner|clerk|administrator|"
    r"operations|sales|finance|financial|credit|risk|portfolio|investment|"
    r"equity|quantitative|treasury|programme|program|rotation|rotational|"
    r"wholesaler|actuar|modeling|surveillance|solutions|management)\b", re.I)

# A capture that is really boilerplate, a greeting, or a company name.
BAD_START = re.compile(
    r"^(you|your|our|we|us|this|that|it|is|was|has|have|and|with|at|in|for|to|"
    r"of|thank|thanks|dear|hello|hi|please|the following|a career|joining|"
    r"employment|a position|a role|the position|the role|careers?|"
    r"working|opportunities)\b", re.I)

# Requisition ids: leading "R-517749 ", "19600 ", "Req37817 - ", "69959 - ",
# "SEG0000002L" suffixes, trailing "(25010262)", "- 2500123".
REQ_LEAD = re.compile(r"^(?:re\s*:\s*)?(?:req(?:uisition)?\s*(?:id)?\s*[:#]?\s*)?"
                      # The ATS code must be hyphen-glued to the id
                      # ("10073106-WD Credit ..."); a space-separated word is
                      # part of the title ("R-517749 CIB Markets ...").
                      r"[A-Z]{0,4}[-–]?\d{3,}(?:[-–][A-Z]{2,3})?[A-Z]{0,3}\s*[-–:]?\s*", re.I)

# Clause openers that can never occur inside a job title -- everything from
# here on is the sentence continuing, not part of the role.
TAIL_CLAUSE = re.compile(
    r"\s+(?:and\s+(?:thank|we|for|are|is|considering|encourage)|"
    r"has\s+been|have\s+been|"
    r"we\s+(?:are|have|will|would|hope|wanted|appreciate|regret|know|"
    r"received|value|look)|"
    r"and\s+we\b|but\s+|so\s+|because\s+|which\s+|"
    r"in\s+your\s+career|on\s+\w+\s+\d{1,2},\s*\d{4}|"
    r"and\s+for\s+(?:your|applying)|for\s+(?:your|taking)\s+)\b.*$", re.I)

LEAD_ARTICLE = re.compile(r"^(?:the|our|a|an|this|your)\s+", re.I)
# Lead-ins a subject-line capture drags along: "Application Received - Associate,
# Investor Research", "the position of Analyst-Risk Management".
LEAD_PHRASE = re.compile(
    # \b after re/fwd/update: without it, this matched the first two letters
    # of "Research Analyst..." as the "Re:" reply prefix and stripped them,
    # silently turning "Research Analyst" into "search Analyst" and creating
    # a duplicate ABI Research row that couldn't be recognized as the same
    # application already on the sheet.
    r"^(?:application\s+(?:received|confirmation|for)|thank you for applying(?:\s+to)?|"
    r"re\b|fwd\b|update(?:\s+on)?\b)\s*[-–:]?\s*|"
    r"^(?:the\s+)?(?:position|role|opening|opportunity|vacancy)\s+(?:of|for)\s+", re.I)
REQ_TRAIL = re.compile(
    r"\s*[-–(,:]?\s*(?:\(?\s*(?:req(?:uisition)?\s*(?:id)?\s*[:#]?\s*)?"
    r"[A-Z]{0,4}[-–]?\d{4,}[A-Z]{0,3}\s*\)?)\s*$", re.I)
REQ_INFIX = re.compile(r"[-–]?\s*\b[A-Z]{2,4}\d{6,}[A-Z]?\b\s*", re.I)

_STOP = (r"position|role|opportunit(?:y|ies)|opening|req(?:uisition)?|job|"
         r"vacancy|posting")


def clean_html(text: str) -> str:
    # Plain-text ATS mail is CRLF-wrapped. Left in place, a stray \r sits
    # *inside* a capture (the patterns only stop at \n), which is how
    # "Trader I - Taxable Fixed-Income Trading, 106691BR We" happened.
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0‌​]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    # ATS mail is hard-wrapped mid-sentence ("...the position of Business\n
    # Analyst."). Rejoin a line to the next unless it ended a sentence, so a
    # title is never split across the newline the patterns stop at.
    return re.sub(r"(?<![.!?:;])\n[ \t]*(?=[A-Za-z(])", " ", text)


# Ordered most-specific first. Group 1 is always the title.
# Bodies are newline-joined, so `[^\n]` bounds a capture to one line.
_T = r"[^.,;:!?\n|]"        # title chars, stops at sentence punctuation
_TC = r"[^.;:!?\n|]"        # ... but commas allowed ("Analyst, Institutional")

PATTERNS = [
    # Workday audit line: "Job Application: Ian Liu - R-10354883 Analyst Program I on 03/11/2025"
    re.compile(rf"Job Application:\s*[^\n\-–]*[-–]\s*({_TC}{{4,80}}?)\s+on\s+\d{{1,2}}/\d{{1,2}}/\d{{2,4}}", re.I),
    # "your application for the following position: Sales and Trading Assistant - Corporate Bonds."
    re.compile(rf"following position\s*:\s*({_TC}{{4,80}})", re.I),
    # "application for the position of X", "not to take your application any further for the position of X"
    # Runs to end of sentence; TAIL_CLAUSE trims the "and thank you for..." that
    # often follows, which an `\band\b` stop would have cut mid-title
    # ("VIE - Investment and Strategy Analyst").
    re.compile(rf"\bposition of\s+(?:the\s+)?({_TC}{{4,80}}?)\s*(?:\.|\n|$)", re.I),
    # "for applying to the Associate, Client Service, 2025 in New York."
    re.compile(rf"\bfor applying to\s+(?:the|our|a)?\s*({_TC}{{4,80}}?)\s*(?:\.|\n|$)", re.I),
    # "applying for the position of R-517749 CIB Markets Program Analyst - FX Sponsors."
    re.compile(rf"\b(?:{_STOP})\s+of\s+(?:the\s+)?({_TC}{{4,80}}?)\s*(?:\.|\n|$)", re.I),
    # "...application for Trade Desk Analyst, and we are delighted"
    # "...received your application for our Associate Trader (Buy-Side Trading Solutions) role."
    re.compile(rf"\bapplication (?:for|to)\s+(?:our|the|your|this)?\s*({_TC}{{4,80}}?)\s+(?:{_STOP})\b", re.I),
    # "...we received your application for Trade Desk Analyst, and we are..."
    # -- no article, so the capture is anchored by the surrounding words instead.
    re.compile(rf"\bapplication (?:for|to)\s+(?:our|the|your|this)?\s*({_T}{{4,80}}?)\s*(?:,|\.|\n|$)", re.I),
    # "Thanks for applying to our Analyst, Revenue Management at Credigy on March 11"
    re.compile(rf"\bapply(?:ing)?\s+to\s+our\s+({_TC}{{4,80}}?)\s+at\s+", re.I),
    # "Thanks for applying to Graham Capital Management, L.P. for the Portfolio Analyst, Commodities Options position"
    re.compile(rf"\bapply(?:ing)?\s+to\s+.{{2,60}}?\s+for\s+(?:the|our|a)\s+({_TC}{{4,80}}?)\s+(?:{_STOP})\b", re.I),
    # "apply for the Treasury and Funding Analyst position", "applying to the X position"
    re.compile(rf"\bappl(?:y|ied|ying)\s+(?:for|to)\s+(?:the|our|a|this)?\s*({_TC}{{4,80}}?)\s+(?:{_STOP})\b", re.I),
    # "submitting an application to Req37817 - Junior Financial Analyst and for your interest"
    re.compile(rf"\bapplication to\s+({_TC}{{4,80}}?)\s+and\s+for\s+your\s+interest", re.I),
    # "interest in the Rotational Analyst position", "interest in the X role"
    re.compile(rf"\binterest(?:ed)? in\s+(?:the|our|a|joining)?\s*({_TC}{{4,80}}?)\s+(?:{_STOP})\b", re.I),
    # "Thank you for your interest in Fund Finance Solutions Portfolio Management, Analyst (7628)."
    re.compile(rf"\binterest in\s+({_TC}{{4,80}}?)\s*(?:\(\d[\w-]*\))?\s*(?:\.|\n|$)", re.I),
    # "candidacy for the X", "consider you for X.", "moving forward with ... for the X position"
    re.compile(rf"\bcandidacy for (?:the|our)?\s*({_TC}{{4,80}}?)\s*(?:\.|\n|$|\bat\b)", re.I),
    re.compile(rf"\bconsider you for\s+(?:the|our|a)?\s*({_TC}{{4,80}}?)\s*(?:\.|\n|$)", re.I),
    re.compile(rf"\bcandidates for (?:the|our)\s+({_TC}{{4,80}}?)\s+(?:{_STOP})\b", re.I),
    # "high interest in the Index Research Analyst (Governance) ,"
    re.compile(rf"\bin the\s+({_TC}{{4,80}}?)\s*,\s*however", re.I),
    # "with relation to Multi-Asset Discretionary Portfolio - Analyst We have received"
    re.compile(rf"\bin relation to\s+({_T}{{4,80}}?)\s+We\b"),
    re.compile(rf"\bwith relation to\s+({_T}{{4,80}}?)\s+We\b"),
    # "time spent applying to the X position - 25010262"
    re.compile(rf"\bspent applying to (?:the|our)?\s*({_TC}{{4,80}}?)\s+(?:{_STOP})\b", re.I),
]

SUBJECT_PATTERNS = [
    # "Thank You for Applying to Financial Analyst - Managed Care at NYP"
    re.compile(rf"\bapplying to\s+({_TC}{{4,80}}?)\s+at\s+\S", re.I),
    # "Update on your Associate Analyst, Research application at Allstate"
    re.compile(rf"\bon your\s+({_TC}{{4,80}}?)\s+application\b", re.I),
    # "Your application to Analyst at Corporate Risk Solutions, LLC"
    re.compile(rf"\bapplication to\s+({_TC}{{4,80}}?)\s+at\s+\S", re.I),
    # "Application Received for TTSTC- Cash Management Analyst/Trader (00047179)"
    re.compile(rf"\b(?:application|résumé|resume|cv)\s*(?:/\w+)?\s*(?:received|submission)\s+for\s+({_TC}{{4,80}})", re.I),
    # "Update: Job Application - Fixed Income Trading Assistant/... (25010262)"
    re.compile(rf"\bjob application\s*(?:update)?\s*[-–:]\s*({_TC}{{4,80}})", re.I),
    # "Thank You for Applying to BNY - 69959 - Associate Trader (Buy-Side Trading Solutions)"
    re.compile(rf"\bapplying to\s+.{{2,40}}?\s+[-–]\s+({_TC}{{4,80}})", re.I),
    # "Associate Treasury Analyst at Chobani", "Analyst, Investment Manager Research-SEG... at Segal Group Inc"
    re.compile(rf"^({_TC}{{4,80}}?)\s+at\s+\S", re.I),
    # "Thank you for your interest in MUFG Bank, Ltd. : 10073106-WD Credit Portfolio Management Analyst - Commodity Finance"
    re.compile(rf":\s*({_TC}{{6,80}})\s*$", re.I),
    # "Constellation -4089-Trading Assistant has been cancelled"
    re.compile(rf"[-–]\s*({_TC}{{4,80}}?)\s+has been\b", re.I),
]


def _tidy(role: str, company: str = "") -> str:
    role = re.sub(r"\s+", " ", role or "").strip()
    role = role.strip(" -–—,:;!.\"'*|")
    role = TAIL_CLAUSE.sub("", role)
    role = REQ_LEAD.sub("", role)
    role = REQ_TRAIL.sub("", role)
    role = REQ_INFIX.sub(" ", role)
    # "Associate - Valuations at Berkeley Research Group, LLC" -> drop the company
    if company:
        role = re.sub(rf"\s+(?:at|with|,)\s+{re.escape(company)}\b.*$", "", role, flags=re.I)
    # "Risk Analyst role at Fanatics" -> "Risk Analyst"
    role = re.sub(r"\s+(?:position|role|opening|opportunity)\s+(?:at|with)\s+.*$",
                  "", role, flags=re.I)
    role = re.sub(r"\s+(?:position|role|opening|opportunity)$", "", role, flags=re.I)
    for _ in range(2):
        role = LEAD_PHRASE.sub("", role).strip(" -–—,:")
    role = LEAD_ARTICLE.sub("", role)
    # "Wolverine's Entry-Level Trader" -> "Entry-Level Trader"
    role = re.sub(r"^[A-Z][\w&.-]*['’]s\s+", "", role)
    role = re.sub(r"\s{2,}", " ", role)
    return role.strip(" -–—,:;!.\"'*|")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _ok(role: str, company: str = "") -> bool:
    if not (4 <= len(role) <= 80):
        return False
    if BAD_START.match(role) or not TITLEISH.search(role):
        return False
    if re.fullmatch(r"[\W\d]+", role):
        return False
    # "Thank you for your interest in <Company>." captures the company, and a
    # name like "Graham Capital Management" is full of title-ish words.
    c = _norm(company)
    r = _norm(role)
    if c and (r == c or r.startswith(c) or c.startswith(r)):
        return False
    return True


def extract(subject: str, body: str, company: str = "") -> tuple[str, str]:
    """Returns (title, which-pattern-matched) or ('', '')."""
    text = clean_html(body)[:4000]
    for i, rx in enumerate(SUBJECT_PATTERNS):
        m = rx.search(subject or "")
        if m:
            role = _tidy(m.group(1), company)
            if _ok(role, company):
                return role, f"subj[{i}]"
    for i, rx in enumerate(PATTERNS):
        m = rx.search(text)
        if m:
            role = _tidy(m.group(1), company)
            if _ok(role, company):
                return role, f"body[{i}]"
    return "", ""
