"""Learned classifier for 'did this rejection follow a real interview?'.

Mirrors rejection_model.py, but the labels are the interesting part. Training
a model on labels produced by the phrase rules would only teach it the rules
it already has. So the positives come from the calendar instead: if a meeting
with that company sits on the calendar before the rejection arrived, the
interview happened — a fact established without reading a single word of the
email. The model then learns which wording travels with that fact, and
generalizes to phrasings the list in interview_signals never covered.

Rule hits are folded in as extra positives, and rejections from companies you
never met are the negatives.

This is weak supervision: calendar coverage is imperfect, so treat the score
as a second opinion. `rejection_status()` still trusts explicit wording and
calendar evidence outright and only consults this for the leftovers.
"""

import pickle

from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.svm import LinearSVC

import interview_signals

MODEL_FILE = "interview_model.pkl"

# Wording is a weaker label than a calendar entry, so the bar for trusting the
# model alone is high — a false positive mislabels a plain rejection as one you
# interviewed for, which quietly overstates how far you got.
MODEL_THRESHOLD = 0.85


def clean(body: str) -> str:
    return interview_signals.plain_text(body)[:3000]


def build_training_set(days_back: int = 500):
    """Returns (texts, labels) for confirmed rejection emails only."""
    import calendar_client
    import gmail_client
    import sheets
    from company_match import company_mentioned, company_in_sender
    from email.utils import parsedate_to_datetime
    from rejection_scanner import REJECTION_PHRASES, _is_real_rejection

    pq = " OR ".join(f'"{p}"' for p in REJECTION_PHRASES)
    messages = gmail_client.search_messages(
        f"newer_than:{days_back}d ({pq})", include_body=True, max_results=800)

    rows = sheets.list_rows()
    try:
        events = calendar_client.list_events(days_back=days_back)
    except Exception as exc:
        print(f"calendar unavailable ({exc}); falling back to wording labels only")
        events = []

    # company -> dates you actually met them
    meeting_dates: dict[str, list] = {}
    for row in rows:
        company = row["Company"]
        if company in meeting_dates:
            continue
        dates = [calendar_client.event_date(e)
                 for e, _ in calendar_client.company_meetings(company, events)]
        meeting_dates[company] = [d for d in dates if d]

    X, y, seen = [], [], set()
    n_cal = 0
    for msg in messages:
        if msg["id"] in seen or not _is_real_rejection(msg):
            continue
        seen.add(msg["id"])

        try:
            sent = parsedate_to_datetime(msg["date"]).date()
        except Exception:
            sent = None

        haystack = f"{msg['subject']} {msg.get('snippet', '')}".lower()
        label = 0
        for company, dates in meeting_dates.items():
            if not dates:
                continue
            if not (company_mentioned(company, haystack)
                    or company_in_sender(company, msg.get("from", ""))):
                continue
            # A meeting after the rejection is a different process; only
            # earlier ones explain it.
            if any(sent is None or d <= sent for d in dates):
                label = 1
                n_cal += 1
                break

        if label == 0 and interview_signals.post_interview_evidence(msg):
            label = 1

        X.append(clean(msg.get("body", "")))
        y.append(label)

    print(f"labelled {sum(y)} post-interview / {len(y) - sum(y)} plain "
          f"rejections ({n_cal} labelled from the calendar)")
    return X, y


def train(days_back: int = 500, out_path: str = MODEL_FILE):
    X, y = build_training_set(days_back)
    pos, neg = sum(y), len(y) - sum(y)
    if pos < 12 or neg < 12:
        raise SystemExit(
            f"not enough labelled examples to train on ({pos} positive / {neg} negative). "
            "Post-interview rejections are rare by nature — the phrase rules and "
            "calendar evidence still work without this model."
        )

    pipe = make_pipeline(
        TfidfVectorizer(stop_words="english", ngram_range=(1, 2),
                        sublinear_tf=True, min_df=2, max_features=20000),
        CalibratedClassifierCV(LinearSVC(class_weight="balanced"), cv=5),
    )
    folds = min(5, pos, neg)
    f1 = cross_val_score(pipe, X, y, cv=folds, scoring="f1")
    print(f"{folds}-fold CV F1: {f1.mean():.3f} (+/- {f1.std():.3f})")

    pipe.fit(X, y)
    with open(out_path, "wb") as f:
        pickle.dump(pipe, f)
    print(f"saved -> {out_path}")
    return pipe


def load(path: str = MODEL_FILE):
    with open(path, "rb") as f:
        return pickle.load(f)


def predict_proba(model, body: str) -> float:
    return float(model.predict_proba([clean(body)])[0][1])
