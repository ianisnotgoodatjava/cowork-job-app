"""Learned rejection classifier, used alongside the phrase rules.

The rules only fire on wording someone thought to list, so every new ATS
template is a fresh miss. Here there are real labels to learn from: emails
the rules verified as rejections are positives, application confirmations
without decisive wording are negatives. A linear model over word n-grams
generalizes to phrasings the list never covered.

It is a second opinion, not a replacement — `scan` still trusts a rule hit
outright, and only consults the model for emails the rules passed over.
"""


import interview_signals
import text_classifier

MODEL_FILE = "rejection_model.pkl"


def clean(body: str) -> str:
    return interview_signals.plain_text(body)[:3000]


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
    return text_classifier.train_and_save(
        X, y, out_path, min_per_class=25, label="rejection")


def load(path: str = MODEL_FILE):
    return text_classifier.load(path)


def predict_proba(model, body: str) -> float:
    return text_classifier.predict_proba(model, clean(body))
