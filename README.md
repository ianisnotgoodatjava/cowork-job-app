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
