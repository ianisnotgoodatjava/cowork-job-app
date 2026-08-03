"""Learned rejection classifier, used alongside the phrase rules.

The rules only fire on wording someone thought to list, so every new ATS
template is a fresh miss. Here there are real labels to learn from: emails
the rules verified as rejections are positives, application confirmations
without decisive wording are negatives. A linear model over word n-grams
generalizes to phrasings the list never covered.

It is a second opinion, not a replacement — `scan` still trusts a rule hit
outright, and only consults the model for emails the rules passed over.
"""

import pickle
import re
import html

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import make_pipeline

MODEL_FILE = "rejection_model.pkl"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean(body: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", body or ""))).lower()[:3000]


def build_training_set(days_back: int = 500):
    """Positives: rules-verified rejections. Negatives: confirmations."""
    import gmail_client
    from rejection_scanner import REJECTION_PHRASES, _is_real_rejection
    from link_finder import CONFIRMATION_PHRASES

    pq = " OR ".join(f'"{p}"' for p in REJECTION_PHRASES)
    cq = " OR ".join(f'"{p}"' for p in CONFIRMATION_PHRASES)

    rej = gmail_client.search_messages(
        f"newer_than:{days_back}d ({pq})", include_body=True, max_results=600)
    conf = gmail_client.search_messages(
        f"newer_than:{days_back}d ({cq})", include_body=True, max_results=600)

    X, y, seen = [], [], set()
    for m in rej:
        if m["id"] in seen:
            continue
        if _is_real_rejection(m):
            seen.add(m["id"])
            X.append(clean(m.get("body", "")))
            y.append(1)
    for m in conf:
        if m["id"] in seen:
            continue
        # A confirmation that the rules flagged is really a rejection; only
        # take the clean ones as negatives so labels stay trustworthy.
        if not _is_real_rejection(m):
            seen.add(m["id"])
            X.append(clean(m.get("body", "")))
            y.append(0)
    return X, y


def train(days_back: int = 500, out_path: str = MODEL_FILE):
    X, y = build_training_set(days_back)
    pos, neg = sum(y), len(y) - sum(y)
    print(f"training set: {pos} rejections / {neg} non-rejections")
    if pos < 25 or neg < 25:
        raise SystemExit("not enough labelled examples to train on")

    pipe = make_pipeline(
        TfidfVectorizer(stop_words="english", ngram_range=(1, 2),
                        sublinear_tf=True, min_df=2, max_features=20000),
        LogisticRegression(max_iter=2000, class_weight="balanced"),
    )
    # Cross-validated, so the number reported is not the score on data the
    # model already saw.
    f1 = cross_val_score(pipe, X, y, cv=5, scoring="f1")
    print(f"5-fold CV F1: {f1.mean():.3f} (+/- {f1.std():.3f})")

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
