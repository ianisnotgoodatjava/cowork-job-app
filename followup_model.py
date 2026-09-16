"""What Ian's own follow-ups look like, learned from the ones he has sent.

The mirror of style_profile, which deliberately excludes follow-ups: this
module keeps only those. They are a different kind of email and the numbers
say so -- his follow-ups run about half the length of a first touch -- so
conditioning a second touch on first-touch exemplars produces something too
long and too introductory to read like him.

Two things are learned here rather than assumed:

  timing()   when he actually follows up. The measured median is 7 days, not
             the 10 that gets quoted by feel, and the scheduler defaults to
             the measured number.
  exemplars() which of his real follow-ups to show the drafter, retrieved by
             topic through the same index style_profile uses.
"""

import collections
import datetime
import json
import os
import pickle
import re
import statistics
import time

from style_profile import CORPUS, _clean, load

_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")

MODEL_FILE = "followup_model.pkl"
LABELS_FILE = os.path.join("logs", "followup_labels.json")

# Everything from the first quoted-reply header down is the message being
# replied to, not his writing. Training on it would teach the classifier the
# other person's words.
_QUOTE_RE = re.compile(r"\n*On .{5,90}?wrote:.*$", re.S)


def _strip_quote(body: str) -> str:
    return _QUOTE_RE.sub("", _clean(body)).strip()


def _follow_ups(corpus: list[dict]) -> list[dict]:
    return [e for e in corpus if e.get("is_follow_up") and e.get("body")]


def _parse_date(value: str):
    """Corpus dates arrive both as plain dates and as ISO timestamps."""
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        width = 19 if "T" in fmt else 10
        try:
            return datetime.datetime.strptime(value[:width], fmt)
        except ValueError:
            continue
    return None


def timing(path: str = CORPUS) -> dict:
    """How long he waits before a second touch, measured per recipient.

    Pairs each follow-up with the message that preceded it to the same
    address, so the gap is a real interval he chose rather than the age of
    an arbitrary thread. Gaps of 200+ days are dropped: those are a new
    campaign to an old contact, not a follow-up to a live one.
    """
    by_recipient = collections.defaultdict(list)
    for record in load(path):
        to = (record.get("to") or "").lower()
        if to and _parse_date(record.get("date")):
            by_recipient[to].append(record)

    gaps = []
    for records in by_recipient.values():
        records.sort(key=lambda r: _parse_date(r["date"]))
        for i, record in enumerate(records):
            if i and record.get("is_follow_up"):
                gap = (_parse_date(record["date"])
                       - _parse_date(records[i - 1]["date"])).days
                if 0 < gap < 200:
                    gaps.append(gap)

    if not gaps:
        return {}
    gaps.sort()
    return {
        "n": len(gaps),
        "median_days": int(statistics.median(gaps)),
        "p25_days": gaps[len(gaps) // 4],
        "p75_days": gaps[3 * len(gaps) // 4],
    }


def measure(path: str = CORPUS) -> dict:
    """The shape of his nudges, for the drafter to aim at.

    Measured over the verified nudges when they exist. The corpus flag is the
    fallback only, since it mixes in warm replies and drags every number
    toward a different kind of email.
    """
    bodies = nudges() or [_clean(e["body"]) for e in _follow_ups(load(path))]
    if not bodies:
        return {}
    words = [len(b.split()) for b in bodies]
    paragraphs = [len([p for p in b.split("\n\n") if p.strip()]) for b in bodies]
    questions = sum(1 for b in bodies if "?" in b)
    return {
        "n": len(bodies),
        "median_words": int(statistics.median(words)),
        "p25_words": sorted(words)[len(words) // 4],
        "p75_words": sorted(words)[3 * len(words) // 4],
        "median_paragraphs": int(statistics.median(paragraphs)),
        "pct_with_question": round(100 * questions / len(bodies)),
    }


def nudges() -> list[str]:
    """His verified nudges into silence, from the Gmail-labelled set.

    These come from thread state, not the corpus flag, so every one is a
    real second touch to someone who had not replied. That verification
    matters more than volume: the corpus flag would hand the drafter mostly
    warm replies, and one "thanks for getting back to me" sent into silence
    is worse than no exemplar at all.
    """
    texts, labels = _cached_training_set()
    return [t for t, l in zip(texts, labels) if l]


def exemplars(company: str = "", role: str = "", area: str = "",
              k: int = 2) -> list[dict]:
    """The verified nudges closest in topic, to show shape not content.

    Retrieval is a TF-IDF match over the nudge pool itself. No minimum
    length, unlike the first-touch exemplars: brevity is the defining
    feature of these, so filtering short ones out would hide the very thing
    worth copying.
    """
    pool = nudges()
    if not pool:
        return []
    query = " ".join(p for p in (company, role, area) if p).strip()
    if not query or len(pool) <= k:
        picked = pool[:k]
    else:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(stop_words="english").fit(pool + [query])
        sims = (vec.transform(pool) @ vec.transform([query]).T).toarray().ravel()
        order = sims.argsort()[::-1]
        picked = [pool[i] for i in order[:k]]
    return [{"subject": "", "body": body} for body in picked]


def build_training_set(days_back: int = 400, limit: int = 400) -> tuple:
    """Labels his follow-ups by what the thread actually did, from Gmail.

    The corpus flag `is_follow_up` only means "a later message in the thread",
    and in practice most of those are replies to someone who had already
    written back -- the opposite of what a scheduler drafts. The thread knows
    the difference: walking it oldest-first, a send with no inbound message
    before it is a genuine nudge into silence; one with an inbound before it
    is a conversation reply.

    Returns (texts, labels) with 1 = nudge, 0 = reply, and caches them so the
    Gmail walk happens once rather than on every train.
    """
    import gmail_client
    from config import ME_RE, NETWORKING_KEYWORDS

    msgs = gmail_client.search_messages(
        f"in:sent newer_than:{days_back}d {NETWORKING_KEYWORDS}",
        max_results=limit)

    # search_messages returns [] when the API rate-limits rather than raising,
    # which reads identically to "you have no outreach" and silently trains on
    # nothing. Refusing here is the difference between a bad model and an
    # obvious error.
    if not msgs:
        raise RuntimeError(
            "Gmail returned no sent outreach. That is usually rate limiting "
            "rather than an empty mailbox -- wait a minute and re-run.")

    texts, labels, seen = [], [], set()
    for msg in msgs:
        tid = msg.get("threadId") or msg["id"]
        if tid in seen:
            continue
        seen.add(tid)

        # One thread fetch per thread trips Gmail's per-user rate limit at a
        # few hundred; the pause costs seconds and avoids losing the run.
        if len(seen) % 25 == 0:
            time.sleep(2)

        try:
            thread = gmail_client.get_thread(tid, include_body=True)
        except Exception:
            continue

        inbound_before = False
        outbound_count = 0
        for entry in thread:
            mine = bool(ME_RE.search(entry.get("from", "")))
            if not mine:
                inbound_before = True
                continue
            outbound_count += 1
            # The opening email is not a follow-up of any kind.
            if outbound_count == 1:
                continue
            body = _strip_quote(entry.get("body", ""))
            if len(body.split()) < 12:
                continue
            texts.append(body)
            labels.append(0 if inbound_before else 1)

    os.makedirs(os.path.dirname(LABELS_FILE), exist_ok=True)
    with open(LABELS_FILE, "w") as f:
        json.dump({"texts": texts, "labels": labels}, f)
    return texts, labels


def _cached_training_set() -> tuple:
    try:
        with open(LABELS_FILE) as f:
            data = json.load(f)
        return data["texts"], data["labels"]
    except (OSError, ValueError, KeyError):
        return [], []


def train(days_back: int = 400, refresh: bool = False,
          out_path: str = MODEL_FILE) -> None:
    """Fits nudge-vs-reply so unlabelled mail can be sorted without Gmail."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.model_selection import cross_val_score
    from sklearn.pipeline import make_pipeline
    from sklearn.svm import LinearSVC

    texts, labels = ([], []) if refresh else _cached_training_set()
    if not texts:
        texts, labels = build_training_set(days_back)

    if len(set(labels)) < 2:
        print(f"Only one class in {len(labels)} example(s) — nothing to fit.")
        return

    model = make_pipeline(
        TfidfVectorizer(stop_words="english", ngram_range=(1, 2),
                        min_df=2, sublinear_tf=True),
        CalibratedClassifierCV(LinearSVC(class_weight="balanced"), cv=5),
    )
    scores = cross_val_score(model, texts, labels, cv=5, scoring="f1")
    model.fit(texts, labels)
    with open(out_path, "wb") as f:
        pickle.dump(model, f)

    nudges = sum(labels)
    print(f"{len(labels)} labelled ({nudges} nudge / {len(labels)-nudges} reply)")
    print(f"cross-validated F1: {scores.mean():.2f} (+/- {scores.std():.2f})")
    print(f"saved -> {out_path}")


def _load_model():
    try:
        with open(MODEL_FILE, "rb") as f:
            return pickle.load(f)
    except (OSError, pickle.UnpicklingError):
        return None


def is_nudge(text: str) -> bool:
    """True when this reads like a nudge into silence, not a warm reply."""
    model = _load_model()
    if model is None:
        return True  # unfiltered rather than empty when no model is trained
    return bool(model.predict([_strip_quote(text)])[0])


def prompt_block(company: str = "", role: str = "", area: str = "",
                 path: str = CORPUS) -> str:
    """Measured shape plus real examples, for the follow-up drafter."""
    stats = measure(path)
    parts = []
    if stats:
        parts.append(
            "HIS FOLLOW-UPS, MEASURED ({n} of them)\n"
            "- {median_words} words is the median; {p25_words}-{p75_words} is "
            "the normal range. A first touch runs roughly twice that, so this "
            "is much shorter than the original email.\n"
            "- {median_paragraphs} short paragraph(s).\n"
            "- {pct}% end on a question.\n"
            "- He does not re-introduce himself or restate his whole "
            "background. The first email did that and it is in the thread "
            "above.".format(pct=stats["pct_with_question"], **stats)
        )
    picked = exemplars(company, role, area)
    if picked:
        shown = "\n\n---\n\n".join(e["body"] for e in picked)
        parts.append(
            "REAL FOLLOW-UPS HE HAS SENT (for rhythm and length -- do not "
            "copy the specifics)\n\n" + shown
        )
    return "\n\n".join(parts)


def run(path: str = CORPUS) -> None:
    """Report what was learned, so the numbers are inspectable."""
    when, shape = timing(path), measure(path)
    if not shape:
        print("No follow-ups in the corpus. Run outreach_corpus first.")
        return
    print(f"Learned from {shape['n']} follow-up(s) he has sent.\n")
    if when:
        print(f"WHEN he follows up  (from {when['n']} matched pairs)")
        print(f"  median wait   {when['median_days']} days")
        print(f"  usual range   {when['p25_days']}-{when['p75_days']} days\n")
    print("WHAT they look like")
    print(f"  length        {shape['median_words']} words median "
          f"({shape['p25_words']}-{shape['p75_words']} typical)")
    print(f"  paragraphs    {shape['median_paragraphs']}")
    print(f"  ends on a question   {shape['pct_with_question']}%")


if __name__ == "__main__":
    run()
