"""Gmail read access (for scanning rejection emails) and draft creation.
Drafts are never auto-sent — you review and hit Send yourself in Gmail."""

import base64
import mimetypes
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from googleapiclient.discovery import build
from google_auth import get_credentials


_svc = None

# One message fetch per HTTP round trip, run strictly in order, was the whole
# reason a scan took minutes: ~900 messages x ~300ms is five minutes, and a
# full refresh runs four scanners over overlapping sets of the same mail.
# The fetches are independent read-only GETs, so they parallelise cleanly.
#
# 12 workers is well inside Gmail's per-user quota (250 units/sec; a
# messages.get costs 5, so ~50/sec is the ceiling) and leaves room for the
# retry backoff to do its job on the occasional 429.
FETCH_WORKERS = 12

# googleapiclient's underlying httplib2 object is not thread-safe, so each
# worker thread builds and keeps its own service rather than sharing one.
_thread_local = threading.local()


def _thread_service():
    svc = getattr(_thread_local, "svc", None)
    if svc is None:
        svc = build("gmail", "v1", credentials=get_credentials())
        _thread_local.svc = svc
    return svc


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
            # A message deleted between the list call and this fetch returns
            # 404. Over a scan of thousands of messages that is close to
            # certain, and it used to abort the whole run — treat it as a
            # message to skip, not an error. 403/429 are rate limits, which
            # are worth the retry.
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status == 404:
                return None
            transient = isinstance(exc, (OSError, TimeoutError)) or status in (403, 429, 500, 502, 503, 504) or any(
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

    def fetch(msg):
        """Fetches one message. Returns None when it should be skipped."""
        worker = _thread_service()
        full = _retry(
            lambda: worker.users().messages().get(
                userId="me", id=msg["id"],
                format="full" if include_body else "metadata",
                metadataHeaders=["Subject", "From", "To", "Date"],
            ).execute()
        )
        # One flaky read used to abort a whole scan after several minutes of
        # work. Losing a single message is a far better outcome.
        if full is None:
            return None
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
        return entry

    # executor.map preserves input order, so callers that relied on results
    # arriving in Gmail's order still get them that way.
    if collected:
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            fetched = list(pool.map(fetch, collected))
    else:
        fetched = []

    results = [entry for entry in fetched if entry is not None]
    skipped = len(fetched) - len(results)
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


# ---------------------------------------------------------------------------
# Draft headers.
#
# Three modules needed "every draft, with its To and Subject" and each grew its
# own copy: draft_cleanup._drafts, findability._draft_index and
# draft_writer._existing_drafts. All three walked the list and then issued one
# drafts().get() per draft -- around 320 requests on this mailbox, at 5 quota
# units each. Running two of them in the same minute is what exhausted the
# per-user quota mid-sweep and left `cleanup-drafts` throwing 403s after the
# dry run had already done the work.
#
# Gmail answers a batch of sub-requests in a single HTTP round trip, so the
# same 320 fetches go out as 4 requests instead of 320. The per-process cache
# then means the second and third caller in one run pay nothing at all.

_DRAFT_CACHE: dict = {}

# Gmail bills each sub-request in a batch separately: drafts.get costs 5 quota
# units and the per-user ceiling is 250 units a second. Batching saves the HTTP
# round trips, not the quota — a 100-wide batch bursts to 500 units at once and
# comes back half 429. 40 per batch with a second between them sits at 200
# units/second, just under the limit, and clears 300 drafts in about eight
# seconds against the minute-plus a sequential walk took.
_BATCH_SIZE = 40
_BATCH_PAUSE = 1.0


def draft_headers(query: str = "", refresh: bool = False) -> list[dict]:
    """[{id, to, subject, date}] for drafts, batched and cached per process.

    `query` is a Gmail search string; passing one narrows the fetch at the
    server rather than filtering afterwards, which is far cheaper when the
    caller only wants one company's drafts.

    Partial results are never returned. A batch sub-request can fail on its
    own -- rate limiting is the common one -- and a callback that quietly
    drops those hands back a short list that looks complete. That is worse
    than an error here: draft_cleanup would read a missing draft as "no draft
    for this company" and findability would report a covered application as
    uncovered. Failures are retried with backoff, and anything still missing
    raises.
    """
    if not refresh and query in _DRAFT_CACHE:
        return _DRAFT_CACHE[query]

    svc = _service()

    def _page(params):
        """One page of the draft list, or a clear error.

        _retry returns None when it gives up, which is right for skipping a
        single message mid-scan but wrong here: a dropped page silently
        shortens the draft list, and every caller reads a missing draft as no
        draft. The quota that matters is per-minute, so the waits are long.
        """
        for wait in (0, 20, 45, 60):
            if wait:
                time.sleep(wait)
            try:
                return svc.users().drafts().list(**params).execute()
            except Exception as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status not in (403, 429, 500, 502, 503, 504):
                    raise
        raise RuntimeError(
            "Gmail would not return the draft list (rate limited). Nothing "
            "was read, so no caller can mistake this for an empty mailbox.")

    ids, token = [], None
    while True:
        params = {"userId": "me", "maxResults": 100, "pageToken": token}
        if query:
            params["q"] = query
        page = _page(params)
        ids += [d["id"] for d in page.get("drafts", [])]
        token = page.get("nextPageToken")
        if not token:
            break

    found: dict[str, dict] = {}
    gone: set[str] = set()
    pending = list(ids)
    for attempt in range(5):
        if not pending:
            break
        if attempt:
            time.sleep(20 * attempt)
        failed: list[str] = []

        def collect(request_id, response, exception):
            if exception is not None or not response:
                # 404 means the draft went away between the list call and this
                # fetch — sent, or deleted in another tab. It is genuinely gone,
                # not a request worth retrying, and it must not hold up the run.
                status = getattr(getattr(exception, "resp", None), "status", None)
                if status == 404:
                    gone.add(request_id)
                else:
                    failed.append(request_id)
                return
            head = {h["name"]: h["value"]
                    for h in response["message"]["payload"].get("headers", [])}
            found[request_id] = {
                "id": request_id,
                "to": (head.get("To") or "").strip(),
                "subject": (head.get("Subject") or "").strip(),
                "date": head.get("Date", ""),
            }

        for begin in range(0, len(pending), _BATCH_SIZE):
            if begin:
                time.sleep(_BATCH_PAUSE)
            batch = svc.new_batch_http_request(callback=collect)
            for draft_id in pending[begin:begin + _BATCH_SIZE]:
                batch.add(svc.users().drafts().get(
                    userId="me", id=draft_id, format="metadata"),
                    request_id=draft_id)
            batch.execute()
        pending = failed

    if pending:
        raise RuntimeError(
            f"Gmail returned {len(found)} of {len(ids) - len(gone)} draft "
            f"headers; {len(pending)} still failing after retries. Refusing to "
            f"hand back a partial list — callers treat a missing draft as no "
            f"draft.")

    out = [found[i] for i in ids if i in found]
    _DRAFT_CACHE[query] = out
    return out
