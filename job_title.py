"""Job titles as text: the two cleanups everything else depends on.

A title arrives carrying things that are not the title -- a requisition
number, the sentence it was cut out of, a company name after "at". Two
functions remove them, and nearly every module needs one or both before it
can compare two applications or write a subject line.

They lived in `dedupe` and `fix_positions`, which also delete sheet rows and
rewrite cells. That put a sheet-maintenance module underneath the scanner and
underneath application identity, which is backwards: these are string
functions with no idea a sheet exists. Reaching up for them was the only
thing stopping the layering from being acyclic.

Pure, and deliberately so. No Gmail, no sheet, no network.
"""

import re

from company_match import normalize as _normalize


_REQ_TAIL_RE = re.compile(
    r"\s*[-–(,]?\s*(job\s*id\s*#?|req(uisition)?\.?\s*(id|no|number)?\s*#?|"
    r"requisition|posting\s*id)\b[\s#:.-]*[\w-]*\)?\s*$", re.I)

def strip_req(title: str) -> str:
    """Drops a trailing job-ID / requisition fragment from a job title."""
    previous, out = None, (title or "").strip()
    while out != previous:
        previous = out
        out = _REQ_TAIL_RE.sub("", out).strip(" -–—,(#:")
    return out


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


_STOP = {"analyst", "associate", "senior", "junior", "the", "and", "for", "at",
         "of", "a", "an", "to", "in", "with", "new", "york", "ny", "i", "ii"}

def _title_tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (title or "").lower())
            if t not in _STOP and len(t) > 2}


def _role_match(a: str, b: str) -> str:
    """How confidently two titles are the same role: '', 'fuzzy', or 'sure'.

    Token overlap alone is not enough to delete on. Fitch's "Structured
    Finance - ABS / CMBS / RMBS, Analyst - New York" titles share every word
    except the one that matters, and a 0.6-overlap rule wanted to merge three
    separate applications into one. So overlap only ever reports a *fuzzy*
    match for you to look at; deletion needs an exact or prefix match.
    """
    na, nb = _normalize(strip_req(a)).strip(), _normalize(strip_req(b)).strip()
    if not na or not nb:
        return ""
    if na == nb:
        return "sure"
    # "Consultant" vs "Consultant at Natixis" — the same title with the
    # company dragged along.
    if na.startswith(nb) or nb.startswith(na):
        return "sure"

    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return ""
    # A word one title has and the other lacks is usually the whole point
    # (ABS vs CMBS, equity vs quantitative), so never treat that as certain.
    overlap = len(ta & tb) / min(len(ta), len(tb))
    return "fuzzy" if overlap >= 0.7 else ""
