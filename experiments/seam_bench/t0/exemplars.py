"""exemplars.py — few-shot exemplar retrieval for the +few-shot systems.

SEAM_TRAINING_EXECUTION_PLAN.md §4 T0 / §7 systems matrix "S1 +few-shot
retrieval": "few-shot retrieval (BM25 over train-split intents, 3
exemplars)". This is a small, dependency-light BM25 implementation
(stdlib only — `re`, `math`, `collections.Counter`) over a corpus of
(item_id, intent, protocol) triples, so it works standalone in this
harness without pulling in rank_bm25 or any other third-party package.

Standard Okapi BM25 (Robertson/Sparck Jones), tokenized by lowercased
alphanumeric runs. This is intentionally the textbook formula, not a
tuned variant — T0 is a baseline measurement, not a retrieval-quality
research question.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Standard Okapi BM25 constants.
_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class ExemplarCandidate:
    item_id: str
    intent: str
    protocol: str


class ExemplarIndex:
    """BM25 index over a corpus of exemplar candidates' intents. Build once
    per (corpus, split-of-origin); query with `top_k` for each item under
    evaluation."""

    def __init__(self, candidates: Sequence[ExemplarCandidate]):
        self.candidates = list(candidates)
        self._doc_tokens: list[list[str]] = [
            tokenize(c.intent) for c in self.candidates]
        self._doc_len = [len(toks) for toks in self._doc_tokens]
        self._avg_doc_len = (
            sum(self._doc_len) / len(self._doc_len) if self._doc_len else 0.0)
        self._doc_freq: Counter[str] = Counter()
        self._term_freq: list[Counter[str]] = []
        for toks in self._doc_tokens:
            tf = Counter(toks)
            self._term_freq.append(tf)
            for term in tf:
                self._doc_freq[term] += 1
        self._n_docs = len(self.candidates)

    def _idf(self, term: str) -> float:
        # BM25's "+0.5 / +0.5" smoothed IDF, floored at a small positive
        # value so an ultra-common term never goes negative and flips sign
        # on the score.
        df = self._doc_freq.get(term, 0)
        raw = math.log((self._n_docs - df + 0.5) / (df + 0.5) + 1.0)
        return raw

    def _score(self, query_tokens: Sequence[str], doc_idx: int) -> float:
        tf = self._term_freq[doc_idx]
        dl = self._doc_len[doc_idx]
        score = 0.0
        for term in query_tokens:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = self._idf(term)
            denom = f + _K1 * (1 - _B + _B * dl / (self._avg_doc_len or 1.0))
            score += idf * (f * (_K1 + 1)) / (denom or 1.0)
        return score

    def top_k(self, query_intent: str, k: int, *,
              exclude_item_ids: Sequence[str] = ()
              ) -> list[ExemplarCandidate]:
        """Return the top-k exemplar candidates by BM25 score against
        `query_intent`, excluding any item_id in `exclude_item_ids` (so an
        item never retrieves itself as its own few-shot exemplar when the
        index was built over a superset that includes it)."""
        if k <= 0:
            return []
        query_tokens = tokenize(query_intent)
        exclude = set(exclude_item_ids)
        scored = [
            (self._score(query_tokens, i), i)
            for i, c in enumerate(self.candidates) if c.item_id not in exclude]
        # Stable order: score desc, then original corpus order (ties broken
        # deterministically rather than by dict/set iteration order).
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [self.candidates[i] for _score, i in scored[:k]]

    def top_k_pairs(self, query_intent: str, k: int, *,
                    exclude_item_ids: Sequence[str] = ()
                    ) -> list[tuple[str, str]]:
        """Same as `top_k` but returns (intent, protocol) pairs — the exact
        shape `Drafter.draft(..., exemplars=...)` expects."""
        return [(c.intent, c.protocol)
                for c in self.top_k(query_intent, k,
                                    exclude_item_ids=exclude_item_ids)]
