"""Prepares an outreach draft for every application nobody has been contacted about.

154 of your open applications have no contact at all. Writing each one by hand
takes about twenty minutes — the good ones in this repo took two web searches
and a judgement call about what to say. That does not scale to 154.

So this does the ninety percent that is mechanical and refuses to fake the
rest:

  * picks the applications where outreach buys the most, ranked by fit with
    your actual background rather than by date;
  * writes the opening, the credential paragraph and the ask, drawing the
    resume bullets that match each posting;
  * leaves exactly one gap — [[HOOK]] — the specific, checkable reason you
    are writing to *this* company.

That gap is deliberate. Every reply you have ever had came from an email with
something particular in it: Paul Schell's AI server work, Lin Ge's move out of
fixed income, Michael Moreno's piece on telecom asset revaluation. A generated
paragraph of enthusiasm reads exactly like the 290 emails that returned a 9.8%
reply rate. Filling [[HOOK]] is a couple of minutes per email and is the only
part that decides whether it works.

Nothing here touches Gmail. It writes files for you to read.
"""

import datetime
import os
import re

import sheets
import status as status_mod
from date_utils import parse_date

OUT_DIR = os.path.join("logs", "drafts", "batch")

# Where this background actually competes, roughly strongest first. Used to
# rank which applications deserve the effort, not to write anything.
FIT_KEYWORDS = {
    3.0: ["rates", "fx", "foreign exchange", "derivative", "swap", "treasury",
          "fixed income", "trading", "trader", "commodit", "energy trading"],
    2.0: ["quantitative", "quant", "research analyst", "equity research",
          "portfolio", "market risk", "structured", "securitiz", "credit"],
    1.0: ["analyst", "capital markets", "investment", "risk", "valuation",
          "asset management", "banking"],
    -2.0: ["retail", "hr ", "human resources", "recruit", "marketing",
           "social media", "customer service", "sales development"],
}


def fit_score(row: dict) -> float:
    """How well this posting matches the background, for ranking only."""
    blob = f"{row.get('Position','')} {row.get('Required Skills & Experience','')}".lower()
    score = 0.0
    for weight, words in FIT_KEYWORDS.items():
        # Count each band once, so "Rates and FX Derivatives Trading" does not
        # outrank a genuinely better fit purely by repeating synonyms.
        if any(word in blob for word in words):
            score += weight
    return score


def _slug(text: str, limit: int = 28) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:limit] or "row"


def _bullets_for(row: dict, bank: dict, n: int = 3) -> list[str]:
    """The resume bullets that best match this posting."""
    import prep_package
    parsed = {
        "requirements": [row.get("Required Skills & Experience", "") or ""],
        "responsibilities": [],
        "text": f"{row.get('Position','')} {row.get('Required Skills & Experience','')}",
    }
    category = prep_package.detect_category(row.get("Position", ""), parsed)
    ranked = prep_package.rank_bullets(bank, parsed, category)
    return [b["text"] for _, b in ranked[:n]]


def _draft(row: dict, bank: dict) -> str:
    company = row.get("Company", "").strip()
    role = row.get("Position", "").strip()
    bullets = _bullets_for(row, bank)
    lead = bullets[0] if bullets else ""

    # The hook used to be a [[HOOK]] placeholder. Now that the sheet carries
    # what the posting actually asks for, the opening can name the specific
    # responsibility and pair it with the matching experience — which is a
    # real paragraph rather than a blank, and gives you something to sharpen
    # instead of something to start from scratch.
    jd = " ".join((row.get("Required Skills & Experience") or "").split())
    # Second line of defence. A description that is really the benefits page
    # produced an opening about the company's childcare policy, which is worse
    # than leaving the paragraph blank.
    try:
        import enrich
        if jd and enrich._is_benefits(jd):
            jd = ""
    except Exception:
        pass
    if jd:
        first_duty = re.split(r"(?<=[a-z])\s+(?=[A-Z])", jd)[0][:190].rstrip(" .,;")
        hook = (
            f"The posting describes {first_duty[0].lower()}{first_duty[1:]}, "
            f"which is the part I wanted to ask you about rather than put to "
            f"a recruiter.\n"
            f"    ^ Sharpen this if you can — one checkable detail about their\n"
            f"      own work beats a line about the posting. But this is a\n"
            f"      real opening as it stands."
        )
    else:
        hook = ("[[HOOK]]\n"
                "    ^ No job description on this row yet, so there is nothing\n"
                "      specific to open with. One paragraph: something they\n"
                "      published or a real question about how their desk works.")

    return f"""To:      [[WHO]]  — find someone on the desk, not HR
Company: {company}
Role:    {role}
Applied: {row.get('Applied Draes', '')}   Location: {row.get('location', '') or '(unknown)'}
Subject: Recent NYU Graduate Inquiring about {role} at {company}

---------------------------------------------------------------------------
Dear [[NAME]],

My name is Ian Liu. I graduated from NYU with a degree in Mathematics and
Economics and currently work at REDACTED. I'm reaching out to learn more about
your work on [[TEAM]] at {company}.

{hook}

{lead}

The common thread is having to size something uncertain and then defend the
assumptions underneath it, which is the part of the work I enjoy most.

If you have 15-20 minutes in the coming weeks, I'd be grateful for the chance
to hear about your experience and how the team approaches its work. I've
attached my resume for reference.

Thank you for your time and consideration.

Best regards,
Ian Liu
icl2016@nyu.edu | REDACTED
---------------------------------------------------------------------------

Other bullets that fit this posting, if the one above is not the right lead:
{chr(10).join('  - ' + b for b in bullets[1:]) or '  (none)'}

When it is ready:
  ./venv/bin/python cli.py draft-outreach-email --row {row['row_number']} \\
      --to <address> --subject "<subject>" \\
      --body-file <this file, body only> --attach ~/Ian_Liu_Resume.pdf
"""


def run(limit: int = 25, min_fit: float = 1.0, out_dir: str = OUT_DIR) -> None:
    import prep_package

    bank = prep_package.load_bank()
    rows = [r for r in sheets.list_rows()
            if status_mod.is_open(r.get("Status", ""))]

    scored = sorted(
        ((fit_score(r), r) for r in rows),
        key=lambda pair: (-pair[0],
                          -(parse_date(pair[1].get("Applied Draes", "")) or
                            datetime.date.min).toordinal()),
    )
    chosen = [(s, r) for s, r in scored if s >= min_fit][:limit]

    os.makedirs(out_dir, exist_ok=True)
    print(f"{len(rows)} open application(s); "
          f"preparing the {len(chosen)} best-fitting\n")

    for score, row in chosen:
        name = f"{row['row_number']:03d}_{_slug(row['Company'])}.txt"
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_draft(row, bank))
        print(f"  fit {score:4.1f} | row {row['row_number']:3d} | "
              f"{row['Company'][:24]:24s} | {row['Position'][:36]}")

    print(f"\nWrote {len(chosen)} draft(s) to {out_dir}/")
    print("Each needs two things from you: a real person to send it to, and\n"
          "the [[HOOK]] paragraph. Everything else is done.")
    print("\nTo find who to write to, `cli.py targets` shows which companies\n"
          "already have contacts and which have none.")
