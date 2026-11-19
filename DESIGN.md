# System redesign

Written after measuring the current code, not from taste. Every problem below
is one that has cost a real application, a real draft, or a real bounce.

## What the system is

One job: keep an accurate record of applications and reach a human at each
company. Gmail is the only source of truth about what happened — the sheet,
the address book and every file in `logs/` are derived from it.

That sentence is the design. The current code does not believe it.

## Measured state

Measured at the start, and again after each step.

| | then | now |
|---|---|---|
| Modules | 79, flat | 82 in 8 layers, checked by a test |
| Layers with an upward import | — | 0 |
| CLI subcommands | 60 | 61 |
| Imports deferred inside functions to break cycles | 45 in `cli.py` | 45, now for startup cost only |
| Confirmation emails the parser cannot read | 18.4% | **4.2%** |
| Offline test corpus for the parser | none | 1,437 real messages |
| Tests | 2 files | 5 files, 134 tests |

The 27% in the first draft of this document was measured differently: it
counted job alerts and account signups, which record no application, as
parser failures. Of 1,437 confirmations in the mailbox, 43 are mail of that
kind and the parser is right to skip them. The honest figure was 18.4%, and
the honest figure now is 4.2%.

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

### 2. The parser is a single point of failure — **fixed**

`app_scanner._parse` read every ATS with one set of subject regexes and
missed 18.4% of the mailbox. 158 of the 247 unread confirmations came from
Workday alone.

*Caused:* Voya, both MUFG credit roles, Blue Cross, ThinkEquity, M Science and
Jefferies all logged by hand.

**What it needed was not more parsers but one measurement.** The rules now
live in `core/confirmation_parser.py`, which touches no network, and
`tests/fixtures/confirmations.json` holds every confirmation in the mailbox,
frozen. `cli.py parser-accuracy` scores the parser against it in two seconds.
Thirteen minutes of Gmail, paid once, replaced the live scan as the only way
to see what a change did.

Measuring first changed what got built. The plan here was one parser per
vendor. The corpus said that of 31 vendor domains sending something
unreadable, 28 hid the employer in one of four places — the subdomain, the
local part, the display name, or the subject — so there is one `ats` parser,
not eight, and the differences are data rather than code. The Workday local
part turned out to be the employer's tenant name, which was sitting in 158
emails that the old rules threw away twice over: the display name was
rejected for containing an "@", and the domain for being a vendor's.

A sender-aware parser also reads roles the generic rules cannot. The generic
rules must refuse "your interest in X", because X is a company about as often
as a role, and a job title in the Company column is worse than no row. Once
the employer is settled, X can only be the role.

Two rules came out of the measurement rather than from taste:

- **A parser rescues; it never overrides.** The first version let the
  sender-aware rules win outright, and they were wrong on 105 messages the
  generic rules had right — "Macquarie" became "Mgl", "Sumitomo Mitsui
  Banking Corporation" became "SMBC", "ProShares" became "Us - Io". A vendor
  identifier is a reliable *identity* and a poor *name*, and the sheet is
  keyed on the name.
- **A refusal says why.** `None` meant both "this was never an application"
  and "this is one and I could not read it", so the nightly log printed one
  list of subjects that never got shorter. Every refusal now carries a
  reason, and the scan prints counts per reason. 96% of the misses were one
  bucket.

Final: 1,109 read before, 1,335 after — 226 gained, 0 lost, no company and no
role changed on any message that was already read.

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
3. ~~**Parser registry** — per-ATS parsers with recorded fixtures.~~ Done,
   and done differently: measure first, then one parser whose differences are
   data. 18.4% → 4.2%.
4. **Collapse the CLI.** 45 of the 60 subcommands are four lines of pure
   delegation and `main()` is 378 lines of repeated argparse. A table with a
   handler named per entry replaces both, makes `--help` navigable by verb,
   and lets a test assert that every handler resolves — which is the one bug
   class here that only shows up when a rarely-run command is finally run.
5. **Delete** what the above makes dead.

Steps 1-3 were where the value was. 4-5 are tidying.

### What is left of the 4.2%

59 messages, and most are not bugs:

- 7 are replies to outreach, not applications. `reply_scanner` reads those;
  this parser should classify them rather than try to read them.
- 10 are Oracle Recruiting, which identifies each pod by a four-letter code
  and names the employer nowhere in the headers. A row called
  "Exvvfasender" is worse than a miss, so these are refused on purpose.
- The rest are single senders that name nobody anywhere: `saashr.com`,
  `clearcompany.com`, `paycomonline.com`.

16 employers are known only by their vendor identifier, so their rows say
"Websteronline" or "Walkerdunlop" and carry a note asking for a name.
`parser-accuracy` lists them; a line in `TENANTS` fixes each one for good.

## What is not changing

The parts that already work, and work because they were built against
measured evidence: the address-convention learner (92% on held-out
addresses), the style profile taken from 346 sent emails, the repeat-contact
guard, the seniority bands, the draft-header cache.
