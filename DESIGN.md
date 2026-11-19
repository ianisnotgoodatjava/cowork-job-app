# System redesign

Written after measuring the current code, not from taste. Every problem below
is one that has cost a real application, a real draft, or a real bounce.

## What the system is

One job: keep an accurate record of applications and reach a human at each
company. Gmail is the only source of truth about what happened — the sheet,
the address book and every file in `logs/` are derived from it.

That sentence is the design. The current code does not believe it.

## Measured state

| | |
|---|---|
| Code | 16,567 lines across 79 flat modules |
| CLI | 120 subcommands |
| Modules importing `sheets` directly | 30 |
| Modules importing `gmail_client` directly | 29 |
| Imports deferred inside functions (to break cycles) | 45 in `cli.py` alone |
| Derived JSON state files in `logs/` | 13 |
| Confirmation emails the parser cannot read | 27% |
| Test files | 2 |

## The five faults, with the damage each caused

### 1. Company identity has no canonical form

Three functions normalise a company name and they disagree:

```
Fitch Group       normalize→"fitch"           Fitch Ratings→"fitch ratings"
S&P Global        _core→"sp"                  slug→"sp"
Moody's WorkWise  →"moodys workwise"          Moody's→"moodys"
```

`_core("S&P Global")` is `"sp"`. A two-character key matches hundreds of firms.

*Caused:* the duplicate Moody's row; AlphaSights resolving to
`alphacapital.online`; "Fitch Group" and "Fitch Ratings" never colliding;
`Risk Analyst at Fanatics` stored as a company.

**Fix.** One `Company` value object. A single `key()` with a minimum length,
an explicit alias table for the cases no rule can reach (`Moody's WorkWise` →
`Moody's`), and `_core`/`slug`/`normalize` deleted. Identity is decided in one
place or it is decided inconsistently everywhere.

### 2. The parser is a single point of failure

`app_scanner._parse` reads every ATS with one set of subject regexes and
misses 27% — 56% for Workday, which writes the role in the body and lets each
employer invent its own subject.

*Caused:* Voya, both MUFG credit roles, Blue Cross, ThinkEquity, M Science and
Jefferies all logged by hand.

**Fix.** A registry of per-source parsers — Workday, Greenhouse, iCIMS,
SuccessFactors, ADP, LinkedIn — chosen by sender, each free to read the body.
A generic fallback stays for the rest. Every parser is a pure function from
message to `Application | None`, so each gets a test with a real email.

### 3. No data layer

29 and 30 modules reach Gmail and Sheets directly. Quota, retry, batching and
caching are therefore reimplemented per call site, badly.

*Caused:* the same N+1 fixed three times; `cleanup-drafts` exhausting the
quota mid-sweep and leaving the Voya decision half-made.

**Fix.** `store/` is the only code that touches the network. `GmailStore`,
`SheetStore`. Everything above takes a store and can be tested with a fake,
which is also what makes parsers testable offline.

### 4. Derived data is treated as source

The sheet is built from Gmail, and the code reads it as authoritative.

*Caused:* deleting the Voya draft — the sheet said the company was closed, the
mailbox had a newer application the parser had never read.

**Fix.** Make it structural, not a guard bolted on. `logs/` becomes a cache
directory that can be deleted and rebuilt from Gmail. Anything that cannot be
rebuilt — corrections, the alias table — moves out of `logs/` and into
versioned project data.

### 5. No layering

`cli.py` imports 63 modules and defers 45 of them inside functions to break
cycles. `bulk_draft` → `draft_writer` → `contact_finder` → back.

**Fix.** Four layers, imports only ever downward:

```
cli/          thin argument parsing, no logic
flows/        scan, reconcile, source, draft  (the four verbs)
domain/       Company, Application, Contact, Draft + rules
store/        Gmail, Sheets, cache
```

## The four verbs

120 subcommands are variations on four things:

- **scan** — read Gmail, produce Applications and Decisions
- **reconcile** — make the sheet match what scan found
- **source** — find reachable people at a company
- **draft** — write an email to one of them

Each is one entry point with options. The rest become flags or disappear.

## Order of work

Each step leaves the system working and is independently useful.

1. **`domain/company.py`** — one identity, alias table, tests from the real
   sheet. Nothing else changes yet. Highest value: it is the root of four
   recurring bugs and it is self-contained.
2. **`store/`** — move Gmail and Sheets behind two classes. Mechanical.
   Unlocks offline tests for everything above it.
3. **Parser registry** — per-ATS parsers with recorded fixtures. Closes the
   27%.
4. **Collapse the CLI** to the four verbs.
5. **Delete** what the above makes dead.

Steps 1-3 are where the value is. Steps 4-5 are tidying and can wait.

## What is not changing

The parts that already work, and work because they were built against
measured evidence: the address-convention learner (92% on held-out
addresses), the style profile taken from 346 sent emails, the repeat-contact
guard, the seniority bands, the draft-header cache.
