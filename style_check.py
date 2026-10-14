"""Scores a drafted email against the corpus, and says where it drifts.

Reinforcement learning is the wrong tool for this, and it is worth saying
why rather than half-building it. RL needs a reward per generation, and the
only outcome available here is reply / no reply: 345 emails, no reply
labels populated at all, and even fully labelled it would be roughly 38
positives. Worse, it is confounded -- Fitch replied twice and KBRA never,
on near-identical text, because replies depend on who was written to far
more than on how. Optimising that signal teaches recipient selection while
appearing to teach style.

The tractable version of the same idea is a discriminator: learn what
separates his writing from generated writing, and use it as a gate. The
separation turned out to be blunt and measurable rather than subtle.
Comparing 12 generated drafts against 345 real emails:

  signature   every generated draft ended with his contact block.
              That appears in 0 of 345 real ones -- he signs off "Best
              regards, Ian Liu" and stops. A perfect discriminator on its
              own, and pure model invention.
  length      199 words median generated against 159 real. Bounded at the
              95th percentile (229) rather than the interquartile range --
              an early cut using 139-171 flagged three quarters of his own
              emails, since by definition half of them fall outside it.
  vocabulary  "problem", "view", "question", "partly", "hoped", "liked",
              "you'd" all appear repeatedly in generated text and never in
              his. So does the bank he asked twice not to mention.

None of that needed a trained model to find, and a rule that names the
specific failure is more useful at generation time than a probability.
"""

import re


# Present in generated drafts, absent from all 345 real emails.
def _never_used() -> tuple:
    """Terms that must not appear in a draft -- his own contact details, and a
    bank he asked twice not to be named. They are personal, so they come from
    resume_bank.json rather than this file."""
    import resume_bank
    return tuple(t.lower() for t in
                 resume_bank.load().get("background", {}).get("never_use", []))

# Words the generator reaches for that he does not. Not banned outright --
# one is fine, a cluster means the register has drifted.
_MODEL_TELLS = (
    "problem", "view", "partly", "hoped", "liked", "you'd", "question",
    "rotated", "quant", "publishing",
)

# From the corpus: 310 of 345 sign off exactly this way.
_SIGN_OFF_RE = re.compile(
    r"(best regards|sincerely|best|warm regards)\s*,?\s*\n\s*ian\s+liu\s*$",
    re.I)

# 5th and 95th percentile of the corpus, not the interquartile range. The
# IQR is the middle half by construction, so using it as a limit rejected
# 75% of his real emails.
WORDS_LOW, WORDS_HIGH = 129, 229


def _clean(body: str) -> str:
    # CRLF normalised first: all 345 corpus emails use it, and a sign-off
    # regex written with \n matched none of them.
    text = (body or "").replace("\r\n", "\n")
    return re.sub(r"[᐀-ᙿ]", "", text).strip()


def check(body: str, subject: str = "") -> list[str]:
    """Every way this draft departs from the corpus. Empty means it passes."""
    text = _clean(body)
    lower = text.lower()
    problems = []

    for token in _never_used():
        if token in lower:
            problems.append(
                f'contains "{token}" — appears in 0 of 345 emails he has sent')

    words = len(text.split())
    if words > WORDS_HIGH:
        problems.append(f"{words} words — his typical range is "
                        f"{WORDS_LOW}-{WORDS_HIGH}, median 159")
    elif words < WORDS_LOW:
        problems.append(f"{words} words — shorter than anything he sends")

    if not _SIGN_OFF_RE.search(text):
        problems.append('does not end "Best regards,\\nIan Liu" — 310 of 345 do')

    hits = [w for w in _MODEL_TELLS if re.search(rf"\b{w}\b", lower)]
    if len(hits) >= 3:
        problems.append("register drift — uses " + ", ".join(hits[:5])
                        + "; these are generator habits, rare or absent in his")

    if subject and not re.match(r"(?i)^(re:\s*)?recent nyu graduate "
                                r"(inquiring about|interested in)\b", subject):
        problems.append(f'subject "{subject[:44]}" is off-template — 88% are '
                        f'"Recent NYU Graduate Inquiring about X at Y"')
    return problems


def run(path: str = "") -> None:
    """Checks drafted emails on disk, or reports the rules."""
    if not path:
        print(__doc__.strip())
        return
    with open(path) as f:
        body = f.read()
    problems = check(body)
    if not problems:
        print("passes — nothing separates this from the corpus")
        return
    for problem in problems:
        print(f"  ! {problem}")
