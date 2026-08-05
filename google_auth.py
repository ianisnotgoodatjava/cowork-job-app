"""Shared OAuth flow for Gmail + Sheets. Run any CLI command once and it will
open a browser for consent, then cache the token in token.json."""

import json
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Every call and chat you had was on the calendar, so an event naming a company
# is direct proof you spoke to them — better evidence than any email wording.
# Kept out of SCOPES on purpose: adding a scope invalidates the cached token,
# and a browser consent you have to click is not something the nightly run can
# do at 3am. `cli.py enable-calendar` grants it when you are actually there.
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


def _cached_scopes() -> set[str]:
    """Scopes the saved token was actually granted.

    Read from the file, not from the Credentials object:
    `from_authorized_user_file(path, SCOPES)` stamps whatever SCOPES you pass
    onto the object, so asking it what it has just echoes what you asked for.
    """
    try:
        with open(TOKEN_FILE) as f:
            return set(json.load(f).get("scopes") or [])
    except (OSError, ValueError):
        return set()


def has_calendar_access() -> bool:
    return CALENDAR_SCOPE in _cached_scopes()


def get_credentials(extra_scopes: list[str] | None = None) -> Credentials:
    # Ask for whatever the cached token already has, so a token that was
    # upgraded to include Calendar keeps that scope on refresh instead of
    # silently dropping back to the base set.
    wanted = sorted(set(SCOPES) | _cached_scopes() | set(extra_scopes or []))

    creds = None
    if os.path.exists(TOKEN_FILE):
        granted = _cached_scopes()
        # A token cached before a scope was added is still "valid", but
        # refreshing it against the wider list fails with `invalid_scope`, and
        # using it fails later with an opaque 403. Re-consent instead.
        if granted and not set(wanted).issubset(granted):
            missing = ", ".join(sorted(set(wanted) - granted))
            print(f"Cached token is missing: {missing}\n"
                  "Opening a browser to re-authorize (one time).")
        else:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, wanted)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"{CREDENTIALS_FILE} not found. Download OAuth client "
                    "credentials from Google Cloud Console (see README.md) "
                    "and place them in the project root."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, wanted)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return creds
