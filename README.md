# Job Application Tracker

Logs job applications to a Google Sheet, helps you find a contact at the
company (manually — no LinkedIn automation), and creates Gmail **drafts**
for application/outreach emails so you always review before sending.

## What this deliberately does NOT do

- **No LinkedIn scraping or automation.** LinkedIn's Terms of Service
  prohibit automated data collection, and violating it risks your account.
  `linkedin_helper.py` only builds a search URL — you browse and pick the
  contact yourself, then save what you found with `save-contact`.
- **No auto-sending email.** Every email is created as a Gmail *draft*.
  You always click Send yourself in Gmail.

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

### 4. First run (authorizes your Google account)
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

`com.example.jobtracker` (a launchd agent) runs `run_auto.sh` at **03:15**,
which runs the full pipeline: new applications, rejections, replies and
networking, Job Description backfill, sort, colour, and the reach-out
reminder email.

**launchd cannot wake a sleeping Mac.** If the machine is asleep at 03:15 the
run is deferred until it next wakes — which is how the 2026-08-03 run died,
firing on wake before the network was up. Each step now retries once and then
skips rather than taking the whole pipeline down, but for it to genuinely run
overnight you need a wake schedule (this needs your password, so run it
yourself):

```bash
sudo pmset repeat wakeorpower MTWRFSU 03:10:00
```

Check it took with `pmset -g sched`. Without that, keep the lid open or leave
the Mac plugged in and awake.

## Running this in Cursor

This is a plain Python project — no MCP server or special Cursor config
needed. Just open this folder in Cursor, open the integrated terminal, and
run the `venv`/`pip install`/`python cli.py ...` commands above like you
would in any terminal.

## Writing the email copy

The CLI intentionally doesn't generate email text itself. The recommended
flow: ask Claude (in chat) to draft the outreach/application email using the
job details from your sheet, save the result to a `.txt` file, then pass it
via `--body-file` to the CLI. Keeps the code simple and keeps you reviewing
every word before it's sent.
