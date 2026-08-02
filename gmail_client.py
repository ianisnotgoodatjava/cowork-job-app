"""Gmail read access (for scanning rejection emails) and draft creation.
Drafts are never auto-sent — you review and hit Send yourself in Gmail."""

import base64
import mimetypes
import os
from email.message import EmailMessage
from googleapiclient.discovery import build
from google_auth import get_credentials


def _service():
    return build("gmail", "v1", credentials=get_credentials())


def search_messages(query: str, max_results: int = 100, include_body: bool = False) -> list[dict]:
    """Returns [{id, subject, from, snippet, date, body?}] for messages matching a Gmail search query."""
    svc = _service()
    results = []
    request = svc.users().messages().list(userId="me", q=query, maxResults=max_results)
    response = request.execute()
    for msg in response.get("messages", []):
        full = svc.users().messages().get(
            userId="me", id=msg["id"],
            format="full" if include_body else "metadata",
            metadataHeaders=["Subject", "From", "To", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in full["payload"]["headers"]}
        entry = {
            "id": msg["id"],
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "snippet": full.get("snippet", ""),
        }
        if include_body:
            entry["body"] = _extract_body(full["payload"])
        results.append(entry)
    return results


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
