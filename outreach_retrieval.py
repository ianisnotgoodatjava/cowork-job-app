"""Retrieval over your past outreach emails.

Given a new target (company, team/area, contact title), finds the past
emails most similar to it, so a new draft can be grounded in what you
actually wrote before rather than a generic template.

Why retrieval and not a trained model: with a few hundred examples and no
labels for "did this work", nearest-neighbour retrieval over TF-IDF vectors
is the technique that actually fits the data. A classifier here would be
fitting noise and reporting confident numbers that mean nothing.
"""

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

import outreach_corpus


class OutreachIndex:
    def __init__(self, records: list[dict]):
        self.records = records
        # Index the *topic* of each email (subject area, company, subject
        # line) rather than the whole body: the bodies share so much boilerplate
        # ("My name is Ian Liu... I recently graduated from NYU") that
        # full-body vectors make everything look equally similar.
        self.corpus = [
            f"{r.get('area', '')} {r.get('company', '')} {r.get('subject', '')}"
            for r in records
        ]
        self.vectorizer = TfidfVectorizer(
            stop_words="english",
            ngram_range=(1, 2),
            sublinear_tf=True,
            min_df=1,
        )
        self.matrix = self.vectorizer.fit_transform(self.corpus)

    def search(self, query: str, k: int = 3, exclude_follow_ups: bool = True) -> list[tuple[float, dict]]:
        vec = self.vectorizer.transform([query])
        scores = cosine_similarity(vec, self.matrix)[0]
        ranked = sorted(zip(scores, self.records), key=lambda p: -p[0])
        out = []
        for score, rec in ranked:
            if exclude_follow_ups and rec.get("is_follow_up"):
                continue
            if score <= 0:
                continue
            out.append((float(score), rec))
            if len(out) >= k:
                break
        return out


def load_index(path: str = outreach_corpus.CORPUS_FILE) -> OutreachIndex:
    return OutreachIndex(outreach_corpus.load(path))


def build_query(company: str = "", area: str = "", title: str = "", role: str = "") -> str:
    return " ".join(p for p in (company, area, title, role) if p)
