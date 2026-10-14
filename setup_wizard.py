"""Gets a new clone from nothing to a working tracker.

Everything this does is documented in the README and can be done by hand; the
point is that it says which step you are on and stops at the first one that
needs you, rather than failing later with a traceback from four modules deep.

Creates nothing you have to undo: the Google Sheet is made in your own Drive
only if you ask for one, and the only file written is .env.
"""

import json
import os
import shutil

import config

ENV_FILE = ".env"
ENV_EXAMPLE = ".env.example"
BANK_FILE = "resume_bank.json"
BANK_EXAMPLE = "resume_bank.example.json"
CREDENTIALS = "credentials.json"


def _ok(label, detail=""):
    print(f"  [ok]    {label}" + (f" — {detail}" if detail else ""))


def _todo(label, detail=""):
    print(f"  [todo]  {label}" + (f"\n          {detail}" if detail else ""))


def check_credentials() -> bool:
    if os.path.exists(CREDENTIALS):
        _ok("Google OAuth credentials", CREDENTIALS)
        return True
    _todo("Google OAuth credentials missing", (
        "Create a project at https://console.cloud.google.com/, enable the\n"
        "          Gmail and Sheets APIs, add yourself as a test user, then make an\n"
        f"          OAuth client ID of type Desktop app and save it as {CREDENTIALS}.\n"
        "          The README has the click-by-click version."))
    return False


def check_env() -> bool:
    if not os.path.exists(ENV_FILE):
        if os.path.exists(ENV_EXAMPLE):
            shutil.copy(ENV_EXAMPLE, ENV_FILE)
            _ok("created .env from the example")
        else:
            _todo("no .env and no .env.example to copy")
            return False
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    if sheet_id and "your_google_sheet" not in sheet_id:
        _ok("GOOGLE_SHEET_ID set")
        return True
    _todo("GOOGLE_SHEET_ID not set", "run `python cli.py setup --create-sheet`")
    return False


def check_resume_bank() -> bool:
    if not os.path.exists(BANK_FILE):
        if os.path.exists(BANK_EXAMPLE):
            shutil.copy(BANK_EXAMPLE, BANK_FILE)
            _todo(f"created {BANK_FILE} from the example — now fill it in",
                  "name, email and your experience; it is gitignored")
        else:
            _todo(f"no {BANK_FILE}")
        return False

    with open(BANK_FILE, encoding="utf-8") as f:
        profile = json.load(f).get("profile", {})
    name, email = profile.get("name", ""), profile.get("email", "")
    if not name or not email or "example.com" in email:
        _todo(f"{BANK_FILE} still has placeholder details",
              "set profile.name and profile.email — they identify your own mail")
        return False
    _ok("resume bank", f"{name} <{email}>")
    return True


def create_sheet(title: str = "Job Applications") -> str:
    """Makes a tracker in your Drive and writes its id into .env.

    New sheets get the canonical column names, not the two awkward spellings
    the original sheet carries. Both are understood on read.
    """
    import sheets

    svc = sheets._service()
    created = svc.spreadsheets().create(body={
        "properties": {"title": title},
        "sheets": [{"properties": {"title": os.environ.get(
            "GOOGLE_SHEET_TAB", "Applications")}}],
    }).execute()
    sheet_id = created["spreadsheetId"]

    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{os.environ.get('GOOGLE_SHEET_TAB', 'Applications')}!A1",
        valueInputOption="RAW",
        body={"values": [config.CANONICAL]},
    ).execute()

    _write_env("GOOGLE_SHEET_ID", sheet_id)
    print(f"\nCreated '{title}' with {len(config.CANONICAL)} columns.")
    print(f"  https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
    print("  id written to .env")
    return sheet_id


def _write_env(key: str, value: str) -> None:
    lines, replaced = [], False
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                if line.startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    replaced = True
                else:
                    lines.append(line)
    if not replaced:
        lines.append(f"{key}={value}\n")
    with open(ENV_FILE, "w") as f:
        f.writelines(lines)


def run(create: bool = False, title: str = "Job Applications") -> None:
    print("Checking what this clone still needs.\n")
    have_creds = check_credentials()
    check_env()
    have_bank = check_resume_bank()

    if create:
        if not have_creds:
            raise SystemExit(
                "\nCannot create a sheet without Google credentials — "
                "do that step first.")
        create_sheet(title)
        print()
        have_sheet = True
    else:
        have_sheet = bool(os.environ.get("GOOGLE_SHEET_ID", "")) and \
            "your_google_sheet" not in os.environ.get("GOOGLE_SHEET_ID", "")

    print()
    if have_creds and have_sheet and have_bank:
        print("Ready. Log your first application:\n")
        print('  python cli.py log-application --company "Acme Corp" \\')
        print('      --job-title "Analyst" --location "New York, NY"')
        print("\nThen `python cli.py sync` to pull applications out of Gmail.")
    else:
        print("Finish the [todo] items above, then run this again.")
