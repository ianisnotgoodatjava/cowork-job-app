"""Gmail read access (for scanning rejection emails) and draft creation.
Drafts are never auto-sent — you review and hit Send yourself in Gmail."""

import base64
import mimetypes
import os
import time
from email.message import EmailMessage
from googleapiclient.discovery import build
from google_auth import get_credentials


_svc = None


def _service():
    # Shared across a run instead of rebuilt on every call — scanners issue
    # thousands of these.
    global _svc
    if _svc is None:
        _svc = build("gmail", "v1", credentials=get_credentials())
    return _svc


def _retry(call, attempts: int = 3, base_delay: float = 2.0):
    """Runs a Gmail call, retrying transient network failures.

    These scans make thousands of sequential requests, so a single SSL read
    timeout is close to certain over a long run — and without this it took the
    whole scan down after minutes of work. Returns None once it gives up.
    """
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            transient = isinstance(exc, (OSError, TimeoutError)) or any(
                s in type(exc).__name__
                for s in ("Timeout", "Connection", "Transport", "SSL", "Incomplete")
            )
            if not transient or attempt == attempts - 1:
                if not transient:
                    raise
                return None
            time.sleep(base_delay * (2 ** attempt))
    return None


def search_messages(query: str, max_results: int = 100, include_body: bool = False) -> list[dict]:
    """Returns [{id, subject, from, snippet, date, body?}] for messages matching a Gmail search query."""
    svc = _service()
    results = []
    # Gmail caps a single page at 500; page through so max_results above that
    # is honoured instead of silently truncating.
    collected = []
    page = None
    while len(collected) < max_results:
        response = _retry(
            lambda: svc.users().messages().list(
                userId="me", q=query, pageToken=page,
                maxResults=min(500, max_results - len(collected)),
            ).execute()
        )
        if response is None:
            break
        collected.extend(response.get("messages", []))
        page = response.get("nextPageToken")
        if not page:
            break

    skipped = 0
    for msg in collected:
        full = _retry(
            lambda: svc.users().messages().get(
                userId="me", id=msg["id"],
                format="full" if include_body else "metadata",
                metadataHeaders=["Subject", "From", "To", "Date"],
            ).execute()
        )
        # One flaky read used to abort a whole scan after several minutes of
        # work. Losing a single message is a far better outcome.
        if full is None:
            skipped += 1
            continue
        headers = {h["name"]: h["value"] for h in full["payload"]["headers"]}
        entry = {
            "id": msg["id"],
            "threadId": msg.get("threadId") or full.get("threadId", ""),
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "snippet": full.get("snippet", ""),
        }
        if include_body:
            entry["body"] = _extract_body(full["payload"])
        results.append(entry)

    if skipped:
        print(f"  ({skipped} message(s) skipped after repeated read errors)")
    return results


def get_thread(thread_id: str, include_body: bool = True) -> list[dict]:
    """Returns a thread's messages, oldest first, shaped like search_messages().

    Thread history is what distinguishes a post-interview rejection from a
    plain one: the rejection itself may say nothing, while the invite that
    scheduled the call sits three messages above it.
    """
    svc = _service()
    thread = _retry(
        lambda: svc.users().threads().get(
            userId="me", id=thread_id,
            format="full" if include_body else "metadata",
            metadataHeaders=["Subject", "From", "To", "Date"],
        ).execute()
    )
    if thread is None:
        return []

    messages = []
    for full in thread.get("messages", []):
        headers = {h["name"]: h["value"] for h in full["payload"].get("headers", [])}
        entry = {
            "id": full["id"],
            "threadId": thread_id,
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "snippet": full.get("snippet", ""),
        }
        if include_body:
            entry["body"] = _extract_body(full["payload"])
        messages.append(entry)
    return messages


def _extract_body(payload: dict) -> str:
    """Prefers text/plain, but falls back to text/html (decoded, tags kept
    intact) since many ATS confirmation emails are HTML-only and the job
    link only exists as an <a href="..."> attribute, not visible text."""
    plain = _extract_mimetype(payload, "text/plain")
    if plain:
        return plain
    return _extract_mimetype(payload, "text/html")


def _extract_mimetype(payload: dict, mimetype: str) -> str:
    if payload.get("mimeType") == mimetype and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _extract_mimetype(part, mimetype)
        if text:
            return text
    return ""


def create_draft(to: str, subject: str, body_text: str, attachments: list[str] | None = None) -> str:
    """Creates a Gmail draft and returns its draft id."""
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body_text)

    for path in attachments or []:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Attachment not found: {path}")
        ctype, encoding = mimetypes.guess_type(path)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(path, "rb") as f:
            message.add_attachment(
                f.read(),
                maintype=maintype,
                subtype=subtype,
                filename=os.path.basename(path),
            )

    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
    svc = _service()
    draft = svc.users().drafts().create(
        userId="me", body={"message": {"raw": encoded}}
    ).execute()
    return draft["id"]
