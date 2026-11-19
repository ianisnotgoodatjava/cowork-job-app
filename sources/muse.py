"""The one place this project talks to a language model: Meta's Muse Spark.

Three steps need a model -- researching a contact and writing the email
(`auto_draft`), finding live postings (`job_finder`), and writing a follow-up
(`followup_scheduler`). They used to reach Claude two different ways, over the
API or through the `claude` CLI, and each carried its own credential checks
and retry loop. Now they share this module, so the key, the model and the
retry policy are decided once.

Meta Model API speaks the OpenAI Responses format, so the client is the
`openai` package pointed at Meta's host. Web search is a built-in tool on the
same request, which is what the two research steps are built around.

Needs MODEL_API_KEY in .env, from https://dev.meta.ai.
"""

import os
import time

BASE_URL = "https://api.meta.ai/v1"
# The non-contributor model: requests to it are not used to train Meta's
# models. The "-contributor" variant is cheaper and is.
MODEL = os.environ.get("MUSE_MODEL", "muse-spark-1.3")


def _key() -> str:
    key = os.environ.get("MODEL_API_KEY", "").strip()
    # .env is copied from .env.example, so an unedited checkout arrives with
    # the placeholder filled in. That is not a key.
    return "" if not key or key.startswith("your_") else key


def available() -> bool:
    """Whether a Muse key is set, so a model step can run at all."""
    return bool(_key())


def require() -> None:
    """Exit with the setup instruction if there is no key."""
    if not available():
        raise SystemExit(
            "No Muse key, so this step cannot run.\n"
            "Get one at https://dev.meta.ai and put MODEL_API_KEY in .env\n"
            "(a few cents per request).")


_client = None


def client():
    global _client
    if _client is None:
        require()
        try:
            from openai import OpenAI
        except ImportError:
            raise SystemExit("pip install openai")
        # A research request runs several searches and a long answer, and
        # routinely outlasts the SDK's default read timeout.
        _client = OpenAI(base_url=BASE_URL, api_key=_key(), timeout=600,
                         max_retries=0)
    return _client


def respond(system: str, prompt: str, web_search: bool = False,
            effort: str = "high", max_attempts: int = 5) -> str:
    """One request, returned as the model's text.

    Rate limits and dropped connections are waited out -- honoring
    Retry-After when the server sends one, otherwise backing off
    exponentially -- so a batch degrades to "slower" instead of "produces
    nothing". Anything else is real and raised.
    """
    import openai

    kwargs = dict(model=MODEL, instructions=system, input=prompt,
                  reasoning={"effort": effort})
    if web_search:
        kwargs["tools"] = [{"type": "web_search",
                            "search_context_size": "high"}]

    for attempt in range(1, max_attempts + 1):
        try:
            response = client().responses.create(**kwargs)
            break
        except (openai.RateLimitError, openai.APIConnectionError,
                openai.InternalServerError) as exc:
            if attempt == max_attempts:
                raise
            retry_after = None
            http = getattr(exc, "response", None)
            if http is not None:
                try:
                    retry_after = float(http.headers.get("retry-after"))
                except (TypeError, ValueError):
                    pass
            wait = retry_after or min(120, 5 * 2 ** (attempt - 1))
            print(f"    {type(exc).__name__} — waiting {wait:.0f}s "
                  f"(attempt {attempt}/{max_attempts})", flush=True)
            time.sleep(wait)

    # A refusal comes back as content, not as an exception, and leaves no
    # text. Surfacing it as an error keeps it from parsing as an empty draft.
    for item in response.output or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", "") == "refusal":
                raise RuntimeError(f"refused: {part.refusal[:120]}")
    if response.status == "incomplete":
        reason = getattr(response.incomplete_details, "reason", "unknown")
        raise RuntimeError(f"incomplete response: {reason}")
    return response.output_text or ""
