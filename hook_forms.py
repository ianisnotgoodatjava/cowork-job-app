"""Several genuinely different ways to write the middle paragraph.

One template produced 44 emails with three distinct openings between them —
7% unique, against 39% across Ian's own 147. Colleagues compare notes, and a
paragraph that is identical but for the nouns reads as a mail merge no matter
how good the sentence is.

The fix is not a longer list of synonyms. Each form below asks for different
inputs and says a different kind of thing, so which one can be used at all
depends on what is actually known about the person and the role. A form whose
inputs are missing is simply unavailable, which is why the selection is not
random: it is the most specific form the evidence supports, and rotation only
breaks ties among equals.

Measured against the corpus, these are the openings Ian actually uses:
  "What interests me about ..."        14x
  "I came across your ..."             12x
  "I'm particularly interested in ..." 12x
  "I've always been interested in ..." 12x
"""

import hashlib


def _pick(options: list, seed: str) -> str:
    """Deterministic choice, so regenerating does not reshuffle the drafts."""
    h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    return options[h % len(options)]


def _trim_title(title: str, company: str) -> str:
    """Drops a company already inside the title.

    Every opening appends "at {company}", so a title stored as "US Economist
    at MUFG" produced "your work as a US Economist at MUFG at MUFG".
    """
    import re
    return re.sub(rf"\s+(?:at|with|of)\s+{re.escape(company)}\s*$", "",
                  (title or "").strip(), flags=re.I).strip(" ,")


def with_title(name: str, title: str, company: str, area: str,
               curiosity: str, seed: str) -> str:
    """Strongest form: their actual role is known."""
    # Six, not three. With three the drafts clustered onto one opening and
    # uniqueness fell to 23% against Ian's 39% — the pool has to be roughly
    # as wide as the number of drafts being written before the seeded pick
    # produces the spread his own writing has.
    title = _trim_title(title, company)
    openings = [
        f"I came across your background and was particularly interested in "
        f"your work as {title} at {company}.",
        f"What interests me about your path is that you are {title} at "
        f"{company}, which is close to the work I am trying to move toward.",
        f"I'm particularly interested in your work as {title} at {company}.",
        f"Your work as {title} at {company} is close to what I have been "
        f"trying to understand properly.",
        f"I noticed you are {title} at {company}, which is the seat I would "
        f"most like to understand better.",
        f"Reading about {company} I kept coming back to your side of it, as "
        f"{title}.",
    ]
    middles = [
        f"I've been spending more time learning about {area}, and "
        f"{curiosity}",
        f"I've always been interested in {area}, and more recently "
        f"{curiosity}",
        f"Most of what I know about {area} is from the outside, and "
        f"{curiosity}",
        f"{area.capitalize()} is what I have been reading toward, and "
        f"{curiosity}",
        f"I have been trying to get a proper feel for {area}, and "
        f"{curiosity}",
        f"The more I read about {area}, the more I "
        f"{curiosity}",
    ]
    closings = [
        "I found your experience especially interesting for that reason.",
        "That is the part I would most like to hear about from someone doing "
        "it.",
        "It is the sort of thing that is hard to judge without having sat in "
        "the seat.",
        "Your perspective on that would be worth more to me than anything I "
        "can read.",
        "I would rather hear it from you than keep piecing it together.",
        "That gap is exactly what I am hoping to close.",
    ]
    return " ".join([_pick(openings, seed + "o"),
                     _pick(middles, seed + "m"),
                     _pick(closings, seed + "c")])


def with_posting(company: str, area: str, duty: str, seed: str) -> str:
    """Second form: the posting is known but the person's role is not."""
    openings = [
        f"What interests me about the {area} work at {company} is the part "
        f"of it that involves having to {duty}.",
        f"I've been reading about the {area} work at {company}, and the part "
        f"that caught my attention was having to {duty}.",
        f"I'm particularly interested in the {area} side of {company}, "
        f"specifically the work of having to {duty}.",
    ]
    middles = [
        "From outside it is hard to tell how much of that is the analysis "
        "itself and how much is arguing the assumptions with people who have "
        "their own view.",
        "What I cannot judge from a posting is which half of that actually "
        "takes the time.",
        "The published version of that work never shows how the judgement "
        "calls were made.",
    ]
    closings = [
        "I would rather ask someone on the team than keep guessing at it.",
        "That is what I would most like to hear about.",
        "I would value hearing how your team approaches it.",
    ]
    return " ".join([_pick(openings, seed + "o"),
                     _pick(middles, seed + "m"),
                     _pick(closings, seed + "c")])


def team_only(company: str, area: str, seed: str) -> str:
    """Weakest form: only the company and the subject area are known.

    Deliberately does not pretend to know anything about the reader. It says
    what Ian is interested in and asks — which is honest, and is also what
    roughly a third of his own emails do.
    """
    openings = [
        f"I've always been interested in {area}, and {company} is one of the "
        f"places where that work is done at scale.",
        f"What interests me about {company} is the {area} side of it.",
        f"I'm particularly interested in {area}, which is what drew me to "
        f"{company}.",
    ]
    middles = [
        "Most of my understanding of it comes from reading rather than from "
        "doing, which is the gap I am trying to close.",
        "I have been learning about it from the outside and would rather "
        "hear how it actually works.",
        "What I know about it is secondhand, and I would like that to change.",
    ]
    closings = [
        "I would be glad to hear what the work actually looks like day to day.",
        "Hearing how someone on your team got into it would be genuinely "
        "useful to me.",
        "I would value 15 minutes of your perspective on it.",
    ]
    return " ".join([_pick(openings, seed + "o"),
                     _pick(middles, seed + "m"),
                     _pick(closings, seed + "c")])
