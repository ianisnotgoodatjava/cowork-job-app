# Job Application Tracker

A command-line tool that runs a job search end to end: it watches Gmail for
applications, rejections and replies, keeps a Google Sheet in sync, finds a
real person to contact at each company, and drafts outreach in your own
writing voice. ~15k lines of Python across 76 modules and 40 CLI commands.

Built for my own search — 600+ applications, 450+ outreach emails — so every
design decision came from something that actually went wrong.

## What it does

| | |
|---|---|
| **Keeps the sheet honest** | Reads Gmail and classifies each thread: applied, rejected, interviewing, replied. Two scikit-learn classifiers catch wording the phrase rules miss. |
| **Finds the contact** | Researches a real mid-level person at the company, verifies the name against published sources, and infers the address from the firm's own pattern — reporting whether it's *published*, *previously delivered to*, or *inferred*. |
| **Writes in your voice** | Drafts are conditioned on a retrieval index over your own past emails, not a generic template. |
| **Follows up once** | Finds outreach that went quiet past *your measured* follow-up interval and drafts exactly one second touch. Never a third. |

## Three things worth a look

**Learning behaviour instead of assuming it** — `followup_model.py`

The follow-up scheduler needed a wait interval. Rather than pick a round
number, it measures: pair each follow-up with the message before it to the
same person, and the median gap is **7 days**, not the 10 I'd have guessed.

Getting the training labels right mattered more than the model. The obvious
label — "is this a later message in the thread?" — is mostly *replies to
people who had already written back*. Training on those produces a follow-up
that opens "Thanks for getting back to me!" addressed to someone who never
did. The labels come from Gmail thread state instead: a send with no inbound
message before it is a genuine nudge into silence. That distinction is the
whole feature.

**Deriving state instead of storing it** — `followup_scheduler.py`

Every rule reads off the live Gmail thread rather than a local state file:
they replied → skip; two sends of mine already → skip; a pending draft →
skip. Nothing can drift out of sync with what was actually sent, and re-runs
are safe by construction.

**Refusing to guess** — `auto_draft.py`

When no contact can be verified, the run writes a `.SKIPPED` file explaining
why instead of inventing a plausible name. A fabricated detail in a cold
email is worse than no email: the recipient notices.

## What this deliberately does NOT do


- **No LinkedIn scraping or automation.** LinkedIn's Terms of Service
  prohibit automated data collection, and violating it risks your account.
  `linkedin_helper.py` only builds a search URL — you browse and pick the
  contact yourself, then save what you found with `save-contact`.
- **No auto-sending email.** Every email is created as a Gmail *draft*.
  You always click Send yourself in Gmail.

## Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -q
```

They cover the parts that decide something — how much an email address can
be trusted, which sheet row a saved contact belongs to, and the hand-typed
date formats — and need no Gmail, Sheet or Claude credential to run. Several
encode a bug that actually shipped: the address-confidence tests exist
because the obvious implementation reads the word "inferred" in a research
note and gets the answer exactly backwards.

## One-time setup

### 1. Google Cloud project + APIs
1. Go to https://console.cloud.google.com/ and create a new project (or use an existing one).
2. Enable these APIs (search each in "APIs & Services" > "Library"):
   - Gmail API
   - Google Sheets API
3. Go to "APIs & Services" > "OAuth consent screen". Choose **External**,
   fill in the required fields, and add your own Google account under
   **Test users** (keeps it in testing mode, no Google review needed).
4. Go to "APIs & Services" > "Credentials" > "Create Credentials" >
   "OAuth client ID". Choose **Desktop app**. Download the JSON.
5. Rename the downloaded file to `credentials.json` and put it in this
   project's root folder (already gitignored — never commit it).

### 2. Google Sheet
1. Create a new Google Sheet (or use an existing tracker).
2. Copy the Sheet ID from its URL:
   `https://docs.google.com/spreadsheets/d/THIS_PART_IS_THE_ID/edit`
3. Copy `.env.example` to `.env` and paste the ID in:
   ```
   cp .env.example .env
   ```
   Edit `.env` and set `GOOGLE_SHEET_ID`. The tab name defaults to
   `Applications` — rename a tab to that, or change `GOOGLE_SHEET_TAB` in `.env`.

### 3. Python environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Your resume

The tool writes emails and tailored resumes from your own text, so it needs
your details. `resume_bank.json` holds them and is gitignored — it carries a
home address, EEO answers and salary expectations, which is not something to
commit.

```bash
cp resume_bank.example.json resume_bank.json
```

Then fill it in. The example has the same shape with placeholder values.
`background.outreach` and `background.job_search` are short bios that go
into the drafting prompts; `background.evidence` maps posting keywords to a
line of your experience.

### 5. First run (authorizes your Google account)
```bash
python cli.py log-application --job-title "Software Engineer" --company "Acme Corp" \
  --requirements "Python, 3+ yrs" --location "Remote" --link "https://acme.com/jobs/123"
```
A browser window will open asking you to sign in and grant access. After
that, a `token.json` is cached so you won't be prompted again.

## Everyday usage

```bash
# 1. Log a new application
python cli.py log-application --job-title "..." --company "..." --requirements "..." --location "..." --link "..."

# 2. See everything you've logged
python cli.py list

# 3. Get a LinkedIn search link, open it yourself, pick a contact
python cli.py find-contact-link --company "Acme Corp"

# 4. Save the contact you found
python cli.py save-contact --row 2 --name "Jane Doe" --title "Engineering Manager" \
  --linkedin-url "https://www.linkedin.com/in/janedoe"

# 5. Draft an outreach email (ask Claude to help write the copy, save it to
#    a text file, then point --body-file at it)
python cli.py draft-outreach-email --row 2 --to jane@acme.com \
  --subject "Quick question about the SWE role" --body-file outreach.txt

# 6. If the job requires emailing your application in directly
python cli.py draft-application-email --row 2 --to jobs@acme.com \
  --subject "Application: Software Engineer" --body-file cover_letter.txt \
  --attach resume.pdf
```

Both draft commands only create a **Gmail draft** — open Gmail, review, and
send it yourself.

## Status detection

The scanners read Gmail (and, if enabled, Calendar) and keep the Status,
Network, Requirements and Job Description columns current.

```bash
# Rejections -> Status=rejected, or interview-rejected (yellow) when the
# email or its thread shows you had already interviewed.
python cli.py scan-rejections --days 460 --dry-run

# Interview invites and real human replies -> Status=interviewing,
# Network=TRUE. Also upgrades an already-rejected row to interview-rejected
# when the thread proves you got to a conversation.
python cli.py scan-replies --days 460 --dry-run

# Email yourself a reminder about anything new that is waiting on a reply.
# Sends only to your own address, resolved from the authenticated account.
python cli.py scan-replies --notify
```

### Warm leads that went cold

The opposite of `follow-ups`. That finds outreach nobody answered; this finds
threads where someone at the company *did* answer and then it went quiet with
no rejection — the leads easiest to lose. Mirae Asset replied twice in June,
went silent, and had never made it onto the sheet at all.

```bash
python cli.py reach-out-again --min-silent 21
```

`WARM` means someone offered a call in that thread. The nightly run emails you
the warm ones.

### Google Calendar (optional, strongest evidence)

Email wording is inference; a calendar event is a record. Grant read-only
access once — it is kept out of the default scopes so the nightly run never
stops on a browser consent:

```bash
python cli.py enable-calendar
```

### The learned models

`scan-rejections` falls back to a classifier when no phrase matches, and a
second model judges whether a rejection followed an interview. The second one
is trained on labels taken from your **calendar**, not from the phrase rules,
so it can learn wording the rules never had:

```bash
python cli.py train-rejection-model
python cli.py train-interview-model     # needs enable-calendar first
```

## Application prep package

Everything up to the submit button. It never logs into an ATS or clicks
anything — you paste, upload and decide.

```bash
# From a URL (works on simple career pages)
python cli.py prep --url "https://..." --row 242

# Workday / LinkedIn / Salesforce block scripted requests or render in
# JavaScript. Open the posting, select all, paste into a .txt, then:
python cli.py prep --text-file posting.txt --row 242

# Cover letters are opt-in
python cli.py prep --text-file posting.txt --row 242 --cover-letter
```

Writes `prep/<row>_<company>_<role>/` containing `job.md` (parsed
requirements), `resume_bullets.md` (which of your real bullets to lead with,
ranked for this posting), `ats_answers.md` (work authorization, why this
company, salary, relevant project) and optionally `cover_letter.md`. It also
fills the row's Job Description, Requirements and Job Link.

It also drops a **tailored one-page PDF resume** in the same folder, built
from `resume_bank.json`. Every line is your own text, copied verbatim —
tailoring is *selection and ordering* (which bullets lead, which get cut to
hold one page), never rewriting. A generator that reworded your experience per
posting would invent a record you cannot defend in the interview. Every role
stays on the page; only bullets are cut, and never a role's last one.

Tailoring is per **category** (equity research, sales & trading, investment
banking, quant/data, risk/credit), not per posting — the same handful of
bullets serves every role in a category. Edit `resume_bank.json` to change the
bullets or their matching tags.

```bash
# Just the resumes, one per category, without a posting
python -c "import resume_pdf; [resume_pdf.build(c) for c in ('equity_research','sales_trading','investment_banking','quant_data','risk_credit')]"
```

```bash
# Fill the Job Description column across the whole sheet
python cli.py backfill-job-descriptions --dry-run
```

## The nightly run

A launchd agent runs `run_auto.sh` on a schedule. Copy the template, set
your own path, and load it:

```bash
sed "s|/ABSOLUTE/PATH/TO/THIS/PROJECT|$PWD|g" jobtracker.plist.example \
  > ~/Library/LaunchAgents/com.example.jobtracker.plist
launchctl load ~/Library/LaunchAgents/com.example.jobtracker.plist
```

The template fires at 06:00, 18:00 and 23:00. Each run does the full
pipeline: new applications, rejections, replies and networking, Job
Description backfill, sort, colour, and the reach-out reminder email.

**launchd cannot wake a sleeping Mac.** If the machine is asleep when a run
is due, it is deferred until the Mac next wakes — which is how the 2026-08-03 run died,
firing on wake before the network was up. Each step now retries once and then
skips rather than taking the whole pipeline down, but for it to genuinely run
overnight you need a wake schedule (this needs your password, so run it
yourself):

```bash
sudo pmset repeat wakeorpower MTWRFSU 03:10:00
```

Check it took with `pmset -g sched`. Without that, keep the lid open or leave
the Mac plugged in and awake.

## Writing the email copy

Two routes, both ending in a draft you review.

**Write it yourself.** Save the text to a `.txt` and pass `--body-file` to
`draft-outreach-email`.

**Let it research and draft.** `auto-draft` finds the contact, verifies them
against published sources, and writes the email conditioned on your own past
outreach:

```bash
python cli.py auto-draft --days 45 --limit 5 --new-only
python cli.py auto-draft --prefer 'real estate|CRE|mortgage'   # a sector first
python cli.py auto-draft --exclude 'some-company'              # leave one out
```

It needs an LLM. With `ANTHROPIC_API_KEY` set it uses the API; without one it
shells out to the `claude` CLI, which runs on a Claude subscription at no
extra cost. Research lands in `logs/drafts/auto/` as text first — turn that
into sheet contacts and Gmail drafts with `stage-drafts`, which re-reads the
saved research rather than paying to research again.

## Following up

```bash
python cli.py schedule-followups            # who is due, nothing written
python cli.py schedule-followups --draft    # draft one second touch each
```

The wait defaults to your own measured median rather than a round number.
See `followup_model.py`; `python followup_model.py` prints what it learned
from your mail.
