"""The shared text-classification pipeline.

Three models here read a piece of your mail and answer yes or no: is this a
rejection, did it follow an interview, is this follow-up a nudge into
silence. They are different questions over the same kind of evidence, and
each had grown its own copy of the same TF-IDF -> calibrated LinearSVC
pipeline, the same cross-validation print, the same pickle and load.

The copies had already drifted. One recomputed the fold count from the class
sizes and one hard-coded five, which only differ when a class is smaller
than five -- exactly the case these models live in, since post-interview
rejections and verified nudges are both rare. The adaptive version is kept.

What stays per-model is what actually differs: how the training set is
built, how many examples are enough, and what to say when there are not.
"""

import pickle


def _safe_folds(y, cap: int = 5) -> int:
    """Outer folds the smaller class can support."""
    pos, neg = sum(y), len(y) - sum(y)
    return max(2, min(cap, pos, neg))


def _calibration_folds(y, outer_folds: int, cap: int = 5) -> int:
    """Inner calibration folds, measured against a *training* fold.

    Cross-validation hands the pipeline (k-1)/k of the data, so the inner
    calibration never sees the whole smaller class -- only that fraction of
    it. Sizing the inner splits against the full count is the subtle version
    of the same off-by-a-fold: it fits fine on everything and then every fold
    fails during scoring, which sklearn reports as "All the k fits failed"
    from deep inside the pipeline.
    """
    smaller = min(sum(y), len(y) - sum(y))
    in_training_fold = smaller * (outer_folds - 1) // outer_folds
    return max(2, min(cap, in_training_fold))


def build_pipeline(calibration_folds: int = 5):
    """TF-IDF over word and bigram features, then a calibrated linear SVM.

    LinearSVC was measured against the alternatives on the rejection data
    and won clearly: 5-fold F1 0.83, against 0.77 for logistic regression
    and 0.26 for Naive Bayes.

    Calibrated because callers threshold on a probability rather than taking
    the label -- a rejection at 0.9 and one at 0.55 are treated differently.
    class_weight balances it: every one of these problems has far more
    negatives than positives.
    """
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import make_pipeline
    from sklearn.svm import LinearSVC

    return make_pipeline(
        TfidfVectorizer(stop_words="english", ngram_range=(1, 2),
                        sublinear_tf=True, min_df=2, max_features=20000),
        # The inner calibration splits the data too, so it has the same
        # floor as the outer cross-validation: with fewer members than folds
        # in either class, every fit fails and sklearn raises from inside the
        # pipeline, which is a long way from the cause.
        CalibratedClassifierCV(LinearSVC(class_weight="balanced"),
                               cv=calibration_folds),
    )


def cross_validated_f1(pipe, X, y) -> tuple:
    """(mean, std, folds). Folds never exceed the smaller class.

    cross_val_score with a fixed cv=5 raises when a class has fewer than
    five members, which is a real possibility here and a confusing way to
    find out your training set is too small.
    """
    from sklearn.model_selection import cross_val_score

    folds = _safe_folds(y)
    scores = cross_val_score(pipe, X, y, cv=folds, scoring="f1")
    return scores.mean(), scores.std(), folds


def train_and_save(X, y, out_path: str, min_per_class: int,
                   too_few_message: str = "", label: str = "example"):
    """Fits, reports a cross-validated F1, and pickles. Returns the pipeline.

    Refuses rather than fitting a model nobody should trust: below
    min_per_class there is not enough signal, and a confident-looking
    classifier trained on a handful of examples is worse than no classifier,
    because the caller believes it.
    """
    pos, neg = sum(y), len(y) - sum(y)
    print(f"training set: {pos} positive / {neg} negative {label}(s)")
    if pos < min_per_class or neg < min_per_class:
        raise SystemExit(too_few_message or (
            f"not enough labelled examples to train on "
            f"({pos} positive / {neg} negative; {min_per_class} of each needed)"))

    outer = _safe_folds(y)
    pipe = build_pipeline(calibration_folds=_calibration_folds(y, outer))
    mean, std, folds = cross_validated_f1(pipe, X, y)
    print(f"{folds}-fold CV F1: {mean:.3f} (+/- {std:.3f})")

    pipe.fit(X, y)
    with open(out_path, "wb") as f:
        pickle.dump(pipe, f)
    print(f"saved -> {out_path}")
    return pipe


def load(path: str):
    """The pickled pipeline, or None when it was never trained.

    None rather than an exception: every caller has a phrase-rule fallback
    and should use it rather than stop.
    """
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except (OSError, pickle.UnpicklingError):
        return None


def predict_proba(model, text: str) -> float:
    """Probability of the positive class for one piece of text."""
    return float(model.predict_proba([text])[0][1])
