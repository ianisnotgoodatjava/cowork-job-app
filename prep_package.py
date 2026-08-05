"""Builds an 'everything up to submit' folder for one application.

Given a job URL it fetches the posting, parses the requirements, ranks your
existing resume bullets against them, and writes a folder you can work
straight out of: which bullets to lead with, answers to the questions every
ATS asks, and the parsed requirements (which also go into the sheet).

It stops at the submit button on purpose. Nothing here logs into an ATS or
clicks anything — you paste, upload, and decide. That keeps the judgment with
you and keeps this clear of bot detection and site terms.

Cover letters are only written when asked for (`--cover-letter`).
"""

import datetime
import json
import os
import re

import job_posting
import sheets

BANK_FILE = "resume_bank.json"
OUT_ROOT = "prep"

# Tailoring is per *category*, not per posting: the same six or seven bullets
# serve every equity research role, and rewriting them per job invents
# experience you do not have. Category picks which bullets lead; the posting's
# own wording reorders them within that.
CATEGORIES = {
    "equity_research": {
        "label": "Equity / Investment Research",
        "lead": ["redacted-research", "mfg-models", "renewable-projectfinance",
                 "redacted-rotation", "canoe-analysis"],
        "keywords": ["research", "equity", "coverage", "analyst", "models",
                     "earnings", "valuation", "sector", "publishing", "notes"],
    },
    "sales_trading": {
        "label": "Sales & Trading / Markets",
        "lead": ["redacted-rotation", "redacted-strategy", "mfg-trading",
                 "redacted-research", "mfg-models"],
        "keywords": ["trading", "sales", "desk", "markets", "derivatives",
                     "rates", "fx", "commodities", "options", "hedging",
                     "execution", "client"],
    },
    "investment_banking": {
        "label": "Investment Banking / Corporate Finance",
        "lead": ["renewable-modeling", "renewable-projectfinance",
                 "redacted-research", "canoe-analysis"],
        "keywords": ["banking", "m&a", "capital raising", "modeling", "dcf",
                     "pitch", "transaction", "coverage", "lbo", "project finance"],
    },
    "quant_data": {
        "label": "Quant / Data / Technology",
        "lead": ["canoe-ml", "redacted-strategy", "mfg-models", "canoe-analysis"],
        "keywords": ["python", "machine learning", "data", "quantitative",
                     "backtest", "statistics", "sql", "engineering",
                     "automation", "modeling", "programming"],
    },
    "risk_credit": {
        "label": "Risk / Credit / Underwriting",
        "lead": ["axaxl-support", "axaxl-pricing", "renewable-projectfinance",
                 "redacted-research"],
        "keywords": ["risk", "credit", "underwriting", "insurance", "exposure",
                     "compliance", "policy", "portfolio risk", "counterparty"],
    },
}

_WORD_RE = re.compile(r"[a-z][a-z+#.]{2,}")


def load_bank(path: str = BANK_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def detect_category(position: str, parsed: dict) -> str:
    """Best-matching category for this posting, by keyword hits."""
    haystack = f"{position} {parsed.get('title', '')} " \
               f"{' '.join(parsed.get('requirements', []))} " \
               f"{' '.join(parsed.get('responsibilities', []))}".lower()
    best, best_score = "equity_research", -1
    for key, spec in CATEGORIES.items():
        score = sum(haystack.count(k) for k in spec["keywords"])
        if score > best_score:
            best, best_score = key, score
    return best


def rank_bullets(bank: dict, parsed: dict, category: str) -> list[tuple[float, dict]]:
    """Scores every bullet against the posting, with a lead-bullet bonus."""
    posting_tokens = _tokens(
        " ".join(parsed.get("requirements", []) + parsed.get("responsibilities", []))
        or parsed.get("text", "")[:4000]
    )
    lead = CATEGORIES[category]["lead"]

    scored = []
    for bullet in bank["bullets"]:
        tag_hits = sum(1 for t in bullet["tags"] if _tokens(t) & posting_tokens)
        text_hits = len(_tokens(bullet["text"]) & posting_tokens) / 12.0
        # Category order is the strong prior; posting wording refines it.
        lead_bonus = (len(lead) - lead.index(bullet["id"])) * 1.5 \
            if bullet["id"] in lead else 0
        scored.append((tag_hits + text_hits + lead_bonus, bullet))
    scored.sort(key=lambda pair: -pair[0])
    return scored


def pick_project(bank: dict, parsed: dict) -> dict:
    posting_tokens = _tokens(" ".join(parsed.get("requirements", []))
                             or parsed.get("text", "")[:3000])
    best, best_score = bank["projects"][0], -1
    for project in bank["projects"]:
        score = sum(1 for t in project["tags"] if _tokens(t) & posting_tokens)
        if score > best_score:
            best, best_score = project, score
    return best


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:limit] or "role"


def _ats_answers(bank: dict, company: str, role: str, parsed: dict,
                 category: str) -> str:
    profile = bank["profile"]
    project = pick_project(bank, parsed)
    reqs = parsed.get("requirements", [])[:4]
    hooks = "\n".join(f"  - {r}" for r in reqs) or "  - (posting listed no explicit requirements)"

    salary = profile.get("salary_expectation") or (
        "**FILL IN** — no target saved. Add `salary_expectation` to "
        "resume_bank.json and this answers itself next time."
    )

    return f"""# ATS answers — {role} @ {company}

Copy-paste ready. Read them before pasting; the "why this company" answer is a
draft built from the posting, not a fact I can verify about you.

## Work authorization
{profile['work_authorization']}

*Short form for dropdowns:* Yes, I am authorized to work in the US. No, I do
not require sponsorship.

## Salary expectation
{salary}

## Why this company / why this role
I am drawn to {company} because this role sits exactly where my training and my
interests meet. My background is quantitative — a mathematics and economics
degree from NYU — but the work I have enjoyed most has been applied: rotating
across rates, EM FX and quant desks at REDACTED, writing daily market
commentary, and building the backtests behind a G10 momentum and carry
strategy. This posting asks for:
{hooks}

That is the work I have been deliberately moving toward, and {company} is a
place where I would be learning it from people doing it at a level I cannot
reach on my own yet.

> Edit this before sending. Add one specific, checkable detail about
> {company} — a deal, a product, a piece of published research, something a
> person said to you — or it reads like every other applicant's answer.

## Tell us about a relevant project
**{project['title']}**

{project['text']}

## Why are you leaving / looking?
I joined REDACTED to get close to how risk is actually priced, and that has been
genuinely useful grounding. I am looking now because I want to apply the
quantitative side of my background more directly — the modelling, research and
markets work I did at REDACTED and have kept up since — and this role does
that in a way my current seat cannot.

## Greatest strength (short answer)
I am comfortable being the person who does the unglamorous data work and then
turns it into something decision-useful — at REDACTED that meant
building ML pipelines over 150,000 documents that saved the analyst teams
20 hours a week.
"""


def _cover_letter(bank: dict, company: str, role: str, parsed: dict,
                  ranked: list) -> str:
    profile = bank["profile"]
    top = [b for _, b in ranked[:3]]
    body = "\n\n".join(f"{b['text']}" for b in top)
    reqs = "; ".join(parsed.get("requirements", [])[:3])
    return f"""{profile['name']}
{profile['location']} | {profile['email']} | {profile['phone']}
{datetime.date.today().strftime('%B %d, %Y')}

Dear Hiring Manager,

I am writing to apply for the {role} position at {company}. I graduated from
NYU with a B.A. in Mathematics and Economics and have spent the time since
working across underwriting, markets and data — the combination this role
appears to call for{(' (' + reqs + ')') if reqs else ''}.

Three things I would bring:

{body}

I would welcome the chance to talk about how this fits what your team needs.
Thank you for your time and consideration.

Sincerely,
{profile['name']}

---
DRAFT — you asked for this one. Read it end to end and cut anything that
overstates what you did. A cover letter that quietly inflates a bullet is
worse than no cover letter.
"""


def generate(url: str = "", row_number: int | None = None, company: str = "",
             role: str = "", cover_letter: bool = False,
             write_sheet: bool = True, out_root: str = OUT_ROOT,
             text_file: str = "", resume: bool = True) -> str:
    bank = load_bank()

    row = None
    if row_number:
        row = sheets.get_row(row_number)
        company = company or row.get("Company", "")
        role = role or row.get("Position", "")

    if text_file:
        with open(text_file, encoding="utf-8") as f:
            parsed = job_posting.parse_text(f.read(), title=role)
        print(f"Parsed pasted posting from {text_file}")
    else:
        print(f"Fetching {url}")
        try:
            parsed = job_posting.parse(job_posting.fetch(url))
        except Exception as exc:
            raise SystemExit(
                f"Could not fetch the posting: {exc}\n"
                "Most ATS pages (Workday, LinkedIn, Salesforce) block scripted\n"
                "requests or render in JavaScript. Open the posting yourself,\n"
                "select all, paste into a .txt file, and re-run with:\n"
                "  --text-file <that file>   (--url is then optional)"
            )

    company = company or parsed.get("company") or "the company"
    role = role or parsed.get("title") or "the role"

    if parsed["thin"]:
        print("  ! The page returned very little text — it is probably a "
              "JavaScript-only posting.\n"
              "    Requirements below may be incomplete; paste the text "
              "manually into job.md if so.")

    category = detect_category(role, parsed)
    ranked = rank_bullets(bank, parsed, category)
    print(f"  category: {CATEGORIES[category]['label']}")
    print(f"  requirements parsed: {len(parsed['requirements'])}")

    folder = os.path.join(
        out_root, f"{row_number or 'new'}_{_slug(company, 24)}_{_slug(role, 32)}")
    os.makedirs(folder, exist_ok=True)

    reqs_md = "\n".join(f"- {r}" for r in parsed["requirements"]) or "_none parsed_"
    resp_md = "\n".join(f"- {r}" for r in parsed["responsibilities"]) or "_none parsed_"
    with open(os.path.join(folder, "job.md"), "w", encoding="utf-8") as f:
        f.write(f"# {role} @ {company}\n\n{url or '(pasted text)'}\n\n"
                f"Category: **{CATEGORIES[category]['label']}**\n\n"
                f"## Requirements\n{reqs_md}\n\n## Responsibilities\n{resp_md}\n")

    with open(os.path.join(folder, "resume_bullets.md"), "w", encoding="utf-8") as f:
        f.write(f"# Resume bullets to lead with — {role} @ {company}\n\n"
                f"Ranked against this posting, within the "
                f"**{CATEGORIES[category]['label']}** ordering.\n"
                f"Use the top 4-6; they are your real bullets, unedited.\n\n")
        for score, bullet in ranked:
            mark = "**LEAD**" if score >= ranked[3][0] else "     "
            f.write(f"{mark} `{score:5.1f}`  _{bullet['company']} — "
                    f"{bullet['role']}_\n  {bullet['text']}\n\n")

    with open(os.path.join(folder, "ats_answers.md"), "w", encoding="utf-8") as f:
        f.write(_ats_answers(bank, company, role, parsed, category))

    if cover_letter:
        with open(os.path.join(folder, "cover_letter.md"), "w", encoding="utf-8") as f:
            f.write(_cover_letter(bank, company, role, parsed, ranked))

    if resume:
        import resume_pdf
        try:
            resume_pdf.build(category, parsed=parsed, company_name=company,
                             role_name=role, out_root=folder)
        except Exception as exc:
            print(f"  ! resume PDF skipped — {type(exc).__name__}: {exc}")

    # The sheet gets the parsed posting, which is what the outreach template
    # reads from.
    if write_sheet and row_number:
        updates = {}
        summary = job_posting.summarize(parsed)
        if summary:
            # The tracker was trimmed to A-J; "Job Description" and
            # "Requirements" are gone and their content now belongs in
            # "Required Skills & Experience". update_row drops unknown
            # columns silently, so writing the old names was a no-op.
            updates["Required Skills & Experience"] = summary
        reqs_col = "Required Skills & Experience"
        if parsed["requirements"] and not (row or {}).get(reqs_col, "").strip():
            updates[reqs_col] = "; ".join(parsed["requirements"][:6])[:900]
        if url and not (row or {}).get("Job Link", "").strip():
            updates["Job Link"] = url
        if updates:
            sheets.update_row(row_number, updates)
            print(f"  sheet row {row_number} updated: {', '.join(updates)}")

    print(f"\nPrep package -> {folder}/")
    for name in sorted(os.listdir(folder)):
        print(f"    {name}")
    if not cover_letter:
        print("  (no cover letter — pass --cover-letter when you want one)")
    return folder
