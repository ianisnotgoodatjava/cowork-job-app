"""Measures how Ian actually writes, from the emails he actually sent.

The generated drafts kept coming out wrong in a way that was obvious in
hindsight: subject lines like "Question on insurer-sponsored private credit
vehicles" when 95% of the real corpus is "Recent NYU Graduate Inquiring
about <area> at <company>". The prompt described the voice in adjectives
("plain, specific, unhurried") and left everything structural to invention.

So this stops describing and starts measuring. It reads the sent corpus and
reports the distribution of the things that were being got wrong -- subject
template, paragraph count, length, opening sentence, closing ingredients --
and auto_draft pastes that straight into the prompt. When the corpus is
rebuilt after more mail goes out, the profile moves with it rather than
going stale in a docstring.

Measured over 346 initial (non-follow-up) emails:
  subject    85% "Recent NYU Graduate Inquiring about X at Y", 10% the same
             with "Interested in". Nothing else clears 1%.
  shape      5 paragraphs in 63% of them, median 152 words
  opening    "I hope you're doing well. My name is Ian Liu, and I recently
             graduated from NYU with a degree in Mathematics and Economics"
  closing    asks for 15-20 minutes (97%), attaches the resume (89%)

Read-only over outreach_corpus.json.
"""

import collections
import json
import re
import statistics

CORPUS = "outreach_corpus.json"

_ARTIFACT_RE = re.compile(r"[᐀-ᙿ]")


def _initial(corpus: list[dict]) -> list[dict]:
    return [e for e in corpus if not e.get("is_follow_up") and e.get("body")]


def _clean(body: str) -> str:
    return _ARTIFACT_RE.sub("", body or "").replace("\r\n", "\n").strip()


def load(path: str = CORPUS) -> list[dict]:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def measure(path: str = CORPUS) -> dict:
    emails = _initial(load(path))
    if not emails:
        return {}

    subjects = collections.Counter()
    openings = collections.Counter()
    paragraphs, words = [], []
    closing = collections.Counter()

    for email in emails:
        subject = re.sub(r"\s+", " ", (email.get("subject") or "").strip())
        if re.search(r"(?i)inquiring about", subject):
            subjects["Recent NYU Graduate Inquiring about <area> at <company>"] += 1
        elif re.search(r"(?i)interested in", subject):
            subjects["Recent NYU Graduate Interested in <area> at <company>"] += 1
        else:
            subjects["other"] += 1

        body = _clean(email["body"])
        blocks = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]
        paragraphs.append(len(blocks))
        words.append(len(body.split()))
        if len(blocks) >= 2:
            openings[re.sub(r"\s+", " ", blocks[1])[:60]] += 1

        tail = " ".join(blocks[-3:]).lower()
        if "15" in tail and "20" in tail:
            closing["asks for 15-20 minutes"] += 1
        if "resume" in tail:
            closing["mentions the attached resume"] += 1
        if "thank you for your time" in tail:
            closing["thanks them for their time"] += 1

    total = len(emails)
    return {
        "n": total,
        "subjects": subjects.most_common(3),
        "paragraphs_mode": collections.Counter(paragraphs).most_common(1)[0],
        "words_median": int(statistics.median(words)),
        "words_range": (sorted(words)[total // 4], sorted(words)[3 * total // 4]),
        "opening": openings.most_common(1)[0] if openings else ("", 0),
        "closing": [(k, v / total) for k, v in closing.most_common()],
    }


def prompt_block(path: str = CORPUS) -> str:
    """The measured style, formatted for pasting into a system prompt."""
    profile = measure(path)
    if not profile:
        return ""

    total = profile["n"]
    top_subject, top_count = profile["subjects"][0]
    para, para_n = profile["paragraphs_mode"]
    low, high = profile["words_range"]
    opening, opening_n = profile["opening"]

    lines = [
        f"HOW IAN ACTUALLY WRITES — measured over {total} emails he has sent.",
        "Match this. It is not a style preference, it is what the corpus says.",
        "",
        f'SUBJECT: "{top_subject}"',
        f"  {top_count / total:.0%} of his subjects. Use this exact shape. The "
        f"area is the\n  desk or topic, not a question and not a headline. Do "
        f"not write subjects\n  like \"Question on X\" or \"Thoughts on Y\" — he "
        f"has never sent one.",
        "",
        f"LENGTH: {para} paragraphs ({para_n / total:.0%} of emails), "
        f"{profile['words_median']} words median,",
        f"  {low}-{high} typical. Going longer reads as a different person.",
        "",
        f"OPENING: begins \"{opening}...\"",
        f"  or a close variant — {opening_n / total:.0%} open this way. Greeting, "
        f"then who he is,\n  then why he is writing. Never open with the hook.",
        "",
        "CLOSING:",
    ]
    for phrase, share in profile["closing"]:
        lines.append(f"  {share:.0%} {phrase}")
    lines.append('  Signs off "Best regards, Ian Liu".')
    return "\n".join(lines)


def run(path: str = CORPUS) -> None:
    block = prompt_block(path)
    if not block:
        print("No corpus. Run: cli.py build-outreach-corpus")
        return
    print(block)


# ---------------------------------------------------------------------------
# Few-shot exemplars.
#
# The measured profile above fixes structure -- subject shape, length, where
# the hook goes. It cannot convey rhythm: how a sentence turns, how much
# hedging goes into a question, how the middle paragraph moves from interest
# to a specific question. Describing that in prose produces the stilted
# register the drafts had.
#
# Showing two real emails does convey it. They are picked by topic so the
# examples come from the same kind of desk, which is what TF-IDF retrieval is
# actually good for.
#
# They are exemplars, never sources. Reusing their content is what produced a
# KBRA draft that said "Fitch" and a management-trainee posting pitched on
# CMBS, so the instruction attached to them says so in as many words.

def exemplars(company: str = "", role: str = "", area: str = "",
              k: int = 2) -> list[dict]:
    """Real past emails closest in topic, to show shape rather than content."""
    try:
        import outreach_retrieval as orx
        index = orx.load_index()
    except Exception:
        return []
    query = orx.build_query(company=company, area=area, title="", role=role)
    try:
        hits = index.search(query, k=k + 3)
    except Exception:
        return []

    out, seen = [], set()
    for _score, record in hits:
        body = _clean(record.get("body", ""))
        # Skip follow-ups and anything too short to show structure.
        if record.get("is_follow_up") or len(body.split()) < 90:
            continue
        key = (record.get("company") or "").lower()
        if key and key in seen:
            continue
        seen.add(key)
        out.append({"subject": record.get("subject", ""), "body": body})
        if len(out) >= k:
            break
    return out


def exemplar_block(company: str = "", role: str = "", area: str = "",
                   k: int = 2) -> str:
    """Exemplars formatted for the prompt, with the do-not-copy warning."""
    picked = exemplars(company, role, area, k)
    if not picked:
        return ""
    parts = [
        "TWO EMAILS IAN ACTUALLY SENT, on the nearest topic to this one.",
        "Match their rhythm, their hedging, and the way the middle paragraph",
        "moves from interest to a specific question.",
        "",
        "Do NOT reuse their content. The company, the desk, the facts and the",
        "hook must all come from your own research for THIS company. Copying",
        "a phrase from these is how a KBRA draft ended up saying \"Fitch\".",
    ]
    for i, mail in enumerate(picked, 1):
        parts += ["", f"--- EXAMPLE {i} ---",
                  f"Subject: {mail['subject']}", "", mail["body"]]
    return "\n".join(parts)
