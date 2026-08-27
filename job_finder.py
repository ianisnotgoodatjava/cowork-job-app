"""Finds live postings worth applying to, and skips the ones already tracked.

The sheet records what has been applied to. It says nothing about what is
open right now. This searches for postings that match the background, filters
out everything already on the sheet, and hands back a ranked shortlist with
direct links.

It deliberately does NOT add rows to the tracker. A recommendation is not an
application, and mixing the two would corrupt every count in the sheet — the
rejection rate, the open-application list, the fit ranking all assume a row
means "I applied to this". Recommendations go to a file; logging one is a
decision you make by running `log-application`.

Needs ANTHROPIC_API_KEY: the searching happens server-side inside the API
call, the same mechanism `auto_draft` uses.
"""

import datetime
import os
import re

import sheets

MODEL = "claude-opus-5"
OUT_DIR = os.path.join("logs", "recommendations")

# Where this background actually competes. Written as search intents rather
# than job titles, because the same work is titled differently at a bank, a
# rating agency and a fund.
SEARCH_AREAS = [
    "rates, FX and derivatives sales/trading analyst",
    "fixed income or credit research analyst",
    "structured finance / securitisation analyst (ABS, CMBS, RMBS, CLO)",
    "equity research associate (semiconductors, technology, or generalist)",
    "energy, power or commodities trading analyst",
    "quantitative research or portfolio analyst",
]

_FIELD_RE = re.compile(r"^===\s*JOB\s*===\s*$", re.M)

SYSTEM = """You find live job postings for Ian Liu and report them plainly.

WHO HE IS
- B.A. Mathematics and Economics, NYU, December 2024.
- Currently Underwriting Assistant, General Property at REDACTED.
- REDACTED Sales & Trading Summer Analyst, Taipei: Rates, EM FX and FX
  Quant desks; daily market commentary; FX volatility with ARMA-GARCH;
  backtested G10 rates momentum and currency carry.
- REDACTED IB intern: project finance, infrastructure, renewables.
- REDACTED: supervised-ML document pipelines in Python.
- Python, R, Excel. Native English and Mandarin. US and Taiwan citizenship —
  no sponsorship needed.

WHAT COUNTS AS A FIT
Entry level to roughly two years of experience. New York preferred, elsewhere
in the US acceptable, remote fine. Front-office or research-facing work in
markets, credit, structured finance, energy/commodities, or quantitative
research. He is not a software engineer and not a generalist consultant.

Reject anything requiring 3+ years, a CFA charter already held, a security
clearance, or sponsorship-free status he does not have (he has it — US
citizen — so that is never a blocker).

RULES
- Only report a posting you have actually seen on a real page in this search.
  Never construct a plausible-looking URL. If you are not sure the posting is
  live, say so in NOTE rather than reporting it as live.
- Prefer the company's own careers site over an aggregator, since aggregator
  links rot faster.
- Do not report the same role twice.

OUTPUT — repeat this block per posting, nothing else between blocks:

=== JOB ===
COMPANY: <company name>
TITLE: <exact posting title>
LOCATION: <city, state — or Remote>
URL: <direct link to the posting itself>
POSTED: <date or "unknown">
WHY: <one sentence on why it fits his background specifically>
NOTE: <anything uncertain — expired, seniority ambiguous, aggregator link>"""


def _parse_jobs(text: str) -> list[dict]:
    """Splits the response into one dict per posting."""
    jobs = []
    for block in _FIELD_RE.split(text)[1:]:
        job = {}
        for line in block.splitlines():
            m = re.match(r"\s*([A-Z]+)\s*:\s*(.+)$", line)
            if m:
                job[m.group(1).lower()] = m.group(2).strip()
        if job.get("company") and job.get("title"):
            jobs.append(job)
    return jobs


def _tracking_key(company: str, title: str) -> str:
    """Identity used to tell a recommendation from something already applied to.

    Requisition suffixes are stripped first: the sheet holds "USD Rates
    Analyst (Req 2026-0022674)" while a posting is titled "USD Rates Analyst",
    and without stripping, an application already made looks brand new.
    """
    from company_match import _core, normalize
    from dedupe import strip_req
    return (f"{_core(company or '').replace(' ', '')}"
            f"|{normalize(strip_req(title or ''))}")


def _already_tracked(rows: list[dict]) -> set:
    from company_match import _core
    keys = set()
    for r in rows:
        keys.add(_tracking_key(r.get("Company", ""), r.get("Position", "")))
        # Bare company key, used only by --new-companies-only.
        keys.add(_core(r.get("Company", "")).replace(" ", ""))
    return keys


def run(areas: list[str] | None = None, per_area: int = 4,
        out_dir: str = OUT_DIR, skip_tracked_companies: bool = False) -> None:
    try:
        import anthropic
    except ImportError:
        raise SystemExit("pip install anthropic")
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise SystemExit(
            "No Anthropic credentials. Set ANTHROPIC_API_KEY in .env.\n"
            "The searching runs server-side inside the API call, so this is\n"
            "the one feature that cannot work without it."
        )

    from company_match import _core, normalize

    client = anthropic.Anthropic()
    rows = sheets.list_rows()
    tracked = _already_tracked(rows)
    areas = areas or SEARCH_AREAS

    print(f"{len(rows)} application(s) already tracked; searching "
          f"{len(areas)} area(s)\n", flush=True)

    seen, found = set(), []
    for area in areas:
        print(f"  searching: {area} ...", flush=True)
        prompt = (
            f"Find up to {per_area} live job postings for: {area}.\n"
            "Focus on New York City, then elsewhere in the US.\n"
            "Report only postings you have actually opened in this search."
        )
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=8000,
                system=SYSTEM,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                tools=[{"type": "web_search_20260209", "name": "web_search",
                        "max_uses": 10}],
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                message = stream.get_final_message()
        except Exception as exc:
            print(f"    ! {type(exc).__name__}: {str(exc)[:120]}")
            continue

        if message.stop_reason == "refusal":
            print("    ! refused")
            continue

        text = "\n".join(b.text for b in message.content if b.type == "text")
        for job in _parse_jobs(text):
            company = _core(job["company"]).replace(" ", "")
            key = _tracking_key(job["company"], job["title"])
            if key in tracked:
                continue
            if skip_tracked_companies and company in tracked:
                continue
            if key in seen:
                continue
            seen.add(key)
            job["area"] = area
            found.append(job)
        print(f"    {len(found)} new so far", flush=True)

    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.date.today().isoformat()
    path = os.path.join(out_dir, f"{stamp}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Job recommendations — {stamp}\n\n"
                f"{len(found)} posting(s) not already on your sheet.\n\n")
        for job in found:
            f.write(f"## {job['title']} — {job['company']}\n"
                    f"- Location: {job.get('location', '?')}\n"
                    f"- Posted: {job.get('posted', 'unknown')}\n"
                    f"- {job.get('url', 'NO URL')}\n"
                    f"- Why: {job.get('why', '')}\n")
            if job.get("note"):
                f.write(f"- Note: {job['note']}\n")
            f.write(f"\n  ```bash\n  ./venv/bin/python cli.py log-application "
                    f"--url \"{job.get('url', '')}\"\n  ```\n\n")

    print(f"\n{len(found)} posting(s) you have not applied to:\n")
    for job in found:
        print(f"  {job['company'][:24]:24s} | {job['title'][:40]:40s} | "
              f"{job.get('location', '?')[:18]}")
        if job.get("note"):
            print(f"      note: {job['note'][:70]}")
    print(f"\n-> {path}")
    print("\nNothing was added to the sheet — a recommendation is not an\n"
          "application. Log one with `cli.py log-application --url ...`.")
