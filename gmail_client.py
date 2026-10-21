"""Gmail read access (for scanning rejection emails) and draft creation.
Drafts are never auto-sent — you review and hit Send yourself in Gmail."""

import base64
import json
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

# Gmail bills every get inside a batch against the same per-second quota:
# 250 units per user per second, and messages.get costs 5, so roughly 50 gets
# per second is the real ceiling however they are packaged. A batch of 60 was
# returning "403 rateLimitExceeded" for 33 of them — which is what the
# "skipped after repeated read errors" lines were, all along. Batching helps
# header-only gets a great deal and full-body gets not at all, so the two
# take different paths below.
BATCH_SIZE = 50          # header-only gets: measured 25 ms/msg, zero drops
BATCH_SIZE_BODY = 25     # full payloads are far larger, so fewer per request
BATCH_WORKERS = 4
BODY_WORKERS = 2         # bodies move more bytes; less concurrency, fewer 403s

# Body fetches are individual gets and the only thing that reduces their loss
# rate is asking for fewer at once and waiting longer when told to. Twelve
# workers with three attempts was losing 96 of 275; the limit being hit is
# "403 rateLimitExceeded", which clears if you back off instead of retrying
# into it.
BODY_FETCH_WORKERS = 5
BODY_RETRY_ATTEMPTS = 6

# Adaptive throttle. A fixed pace was tried and rejected: it slowed a
# 275-message scan from 25 to 113 ms/msg while large scans still failed. The
# real behaviour is that Gmail tolerates a burst and then starts refusing, so
# the only thing that works across both sizes is to go fast until told not
# to, then back off and recover. Without this a 7,000-message harvest lost
# 6,866 of them.
_throttle_lock = threading.Lock()
_throttle = {"delay": 0.0, "clean": 0}
THROTTLE_MAX = 2.0
THROTTLE_STEP = 0.25


def _throttle_wait() -> None:
    with _throttle_lock:
        d = _throttle["delay"]
    if d > 0:
        time.sleep(d)


def _throttle_report(rate_limited: bool) -> None:
    """Speeds up after sustained success, slows down the moment it is refused."""
    with _throttle_lock:
        if rate_limited:
            _throttle["delay"] = min(THROTTLE_MAX,
                                     _throttle["delay"] + THROTTLE_STEP)
            _throttle["clean"] = 0
        else:
            _throttle["clean"] += 1
            if _throttle["clean"] >= 5 and _throttle["delay"] > 0:
                _throttle["delay"] = max(0.0,
                                         _throttle["delay"] - THROTTLE_STEP / 2)
                _throttle["clean"] = 0


_thread_local = threading.local()


def _thread_service():
    """A Gmail client per worker thread.

    googleapiclient's underlying http object is not thread-safe; sharing one
    across workers aborts the interpreter rather than failing cleanly.
    """
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
            ).execute(),
            attempts=BODY_RETRY_ATTEMPTS,
        )
        if response is None:
            # A rate-limited list call used to end the loop quietly, so the
            # caller received an empty result and reported "Found 0" as
            # though the mailbox held nothing. One pass genuinely reported
            # zero rejections and zero job links that way. Say so instead.
            print(f"  ! Gmail search failed after retries; results are "
                  f"incomplete ({len(collected)} id(s) collected so far)"
                  f" for: {query[:70]}")
            break
        collected.extend(response.get("messages", []))
        page = response.get("nextPageToken")
        if not page:
            break

    fmt = "full" if include_body else "metadata"

    def to_entry(msg_id: str, thread_hint: str, full: dict) -> dict:
        headers = {h["name"]: h["value"] for h in full["payload"]["headers"]}
        entry = {
            "id": msg_id,
            "threadId": thread_hint or full.get("threadId", ""),
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "snippet": full.get("snippet", ""),
        }
        if include_body:
            entry["body"] = _extract_body(full["payload"])
        return entry

    def fetch_batch(chunk: list) -> dict:
        """One HTTP request for up to BATCH_SIZE messages.

        Previously each message was its own round trip, 12 at a time. A scan
        of 800 messages was therefore 800 HTTPS requests, which burst past the
        per-user rate limit; the 403s were retried with exponential backoff
        and then dropped, so the scan was both slow and lossy — a query that
        should have returned 843 messages returned none at all.

        Gmail accepts a batch of gets as a single request, so the same scan is
        ~17 requests. Per-message failures come back per-item instead of
        taking anything else down with them.
        """
        out, failed = {}, []

        def handle(request_id, response, exception):
            if exception is not None:
                status = getattr(getattr(exception, "resp", None), "status", None)
                if status != 404:          # 404 = deleted between list and get
                    failed.append(request_id)
                return
            out[request_id] = response

        # Per-thread service, not the shared one: googleapiclient's transport
        # is not thread-safe, and four batch workers sharing one http object
        # aborted the interpreter outright.
        _throttle_wait()
        worker = _thread_service()
        batch = worker.new_batch_http_request(callback=handle)
        for msg in chunk:
            batch.add(
                worker.users().messages().get(
                    userId="me", id=msg["id"], format=fmt,
                    metadataHeaders=["Subject", "From", "To", "Date"],
                ),
                request_id=msg["id"],
            )
        try:
            _retry(batch.execute)
        except Exception:
            failed.extend(m["id"] for m in chunk if m["id"] not in out)
        _throttle_report(bool(failed))
        return {"ok": out, "failed": failed}

    def fetch_one(msg):
        """One message, one request — the path bodies still have to take."""
        _throttle_wait()
        worker = _thread_service()
        full = _retry(
            lambda: worker.users().messages().get(
                userId="me", id=msg["id"], format=fmt,
                metadataHeaders=["Subject", "From", "To", "Date"],
            ).execute(),
            attempts=BODY_RETRY_ATTEMPTS,
        )
        _throttle_report(full is None)
        return msg["id"], full

    fetched_by_id, still_failing = {}, []
    if collected and include_body:
        # Batching full payloads was measured and abandoned: 50-per-request
        # lost 33 of 60 to "403 rateLimitExceeded", and shrinking the batch
        # made it worse (250 of 275 lost), because the quota is charged per
        # get regardless of packaging while the response size grows. One
        # request per message, moderately parallel, is what actually works
        # here. Headers still batch, where it measured 17 ms/msg with no loss.
        with ThreadPoolExecutor(max_workers=BODY_FETCH_WORKERS) as pool:
            for mid, full in pool.map(fetch_one, collected):
                if full is not None:
                    fetched_by_id[mid] = full
    elif collected:
        size = BATCH_SIZE_BODY if include_body else BATCH_SIZE
        workers = BODY_WORKERS if include_body else BATCH_WORKERS
        chunks = [collected[i:i + size]
                  for i in range(0, len(collected), size)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for res in pool.map(fetch_batch, chunks):
                fetched_by_id.update(res["ok"])
                still_failing.extend(res["failed"])

        # One more pass over whatever the batch could not deliver, one at a
        # time. Rate-limit failures usually clear by themselves, and retrying
        # a handful individually is cheap.
        # Rate-limited items are not lost causes — they just arrived too
        # fast. Re-batch them, paced, until nothing new comes back.
        for _ in range(6):
            retry_ids = [i for i in dict.fromkeys(still_failing)
                         if i not in fetched_by_id]
            if not retry_ids:
                break
            time.sleep(1.5)
            still_failing = []
            for i in range(0, len(retry_ids), BATCH_SIZE):
                res = fetch_batch([{"id": m} for m in retry_ids[i:i + BATCH_SIZE]])
                fetched_by_id.update(res["ok"])
                still_failing.extend(res["failed"])

    # Gmail's own ordering is preserved by walking `collected`, not the dict.
    fetched = [
        to_entry(m["id"], m.get("threadId", ""), fetched_by_id[m["id"]])
        if m["id"] in fetched_by_id else None
        for m in collected
    ]

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
            metadataHeaders=["Subject", "From", "To", "Date", "Message-ID"],
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
            # The RFC id a reply's In-Reply-To must point at; distinct from
            # Gmail's own message id above.
            "message_id": headers.get("Message-ID", headers.get("Message-Id", "")),
            "snippet": full.get("snippet", ""),
            # Drafts sit in the thread as messages carrying the DRAFT label;
            # without this a caller cannot tell a sent reply from a pending one.
            "labels": full.get("labelIds", []),
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


def create_draft(to: str, subject: str, body_text: str,
                 attachments: list[str] | None = None,
                 thread_id: str | None = None,
                 in_reply_to: str | None = None) -> str:
    """Creates a Gmail draft and returns its draft id.

    Pass `thread_id` and the original's RFC Message-ID as `in_reply_to` to
    make the draft a reply inside an existing thread. Gmail groups by
    threadId; other mail clients group by In-Reply-To/References, so both
    are set. Without them the draft is a fresh message, which is right for
    first-touch outreach and wrong for a follow-up.
    """
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
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
    payload = {"raw": encoded}
    if thread_id:
        payload["threadId"] = thread_id
    svc = _service()
    draft = svc.users().drafts().create(
        userId="me", body={"message": payload}
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

# Headers kept across runs, keyed by draft id. Listing the ids is cheap -- one
# quota unit a page, 0.9s for three hundred drafts -- while fetching each
# header costs five, which is the whole 13s. Almost none of them changed since
# the last run, so almost none need fetching again.
#
# The stored message id is what makes that safe. Gmail mints a new message id
# every time a draft is edited while the draft id stays put, so a mismatch
# means the body changed and the header has to be read again. Verified rather
# than assumed: editing a probe draft moved its message id.
_DRAFT_STORE = os.path.join("logs", "draft_headers.json")


def _load_draft_store() -> dict:
    try:
        with open(_DRAFT_STORE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_draft_store(store: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_DRAFT_STORE), exist_ok=True)
        tmp = _DRAFT_STORE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(store, f)
        os.replace(tmp, _DRAFT_STORE)
    except OSError:
        pass


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

    ids, message_ids, token = [], {}, None
    while True:
        params = {"userId": "me", "maxResults": 100, "pageToken": token}
        if query:
            params["q"] = query
        page = _page(params)
        for d in page.get("drafts", []):
            ids.append(d["id"])
            message_ids[d["id"]] = (d.get("message") or {}).get("id", "")
        token = page.get("nextPageToken")
        if not token:
            break

    store = {} if refresh else _load_draft_store()
    found: dict[str, dict] = {}
    stale = []
    for draft_id in ids:
        cached = store.get(draft_id)
        if cached and cached.get("message_id") == message_ids.get(draft_id):
            found[draft_id] = {k: v for k, v in cached.items()
                               if k != "message_id"}
        else:
            stale.append(draft_id)

    gone: set[str] = set()
    pending = list(stale)
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

    # Rebuilt from the ids just listed, so drafts deleted elsewhere drop out
    # instead of accumulating forever.
    _save_draft_store({
        i: {**found[i], "message_id": message_ids.get(i, "")}
        for i in ids if i in found})

    out = [found[i] for i in ids if i in found]
    _DRAFT_CACHE[query] = out
    return out
