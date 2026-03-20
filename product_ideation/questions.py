"""
Recurring unanswered question detection.

Identifies questions in review text, embeds them with sentence-transformers,
and clusters similar questions to surface recurring themes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from product_ideation.config import ProductIdeationConfig

log = logging.getLogger("product_ideation.questions")

QUESTION_STARTS = (
    "how", "why", "what", "when", "where", "who", "which",
    "is it", "does it", "can it", "will it", "has anyone",
    "does anyone", "can anyone", "is there", "are there",
    "do they", "can you", "should i",
)


@dataclass
class QuestionCluster:
    """A cluster of similar questions from reviews."""
    representative: str     # Most common phrasing
    questions: list[str]
    frequency: int
    theme: str = ""         # Optional label


def is_question(sentence: str) -> bool:
    """Determine if a sentence is a question."""
    s = sentence.strip()
    if not s:
        return False
    if s.endswith("?"):
        return True
    return s.lower().startswith(QUESTION_STARTS)


def extract_questions(text: str) -> list[str]:
    """Extract question sentences from text."""
    # Split on sentence boundaries
    sentences = re.split(r'[.!?\n]+', text)
    questions = []

    for sent in sentences:
        sent = sent.strip()
        if not sent or len(sent) < 10:
            continue
        if is_question(sent):
            questions.append(sent + ("?" if not sent.endswith("?") else ""))

    return questions


class QuestionClusterer:
    """Clusters similar questions using sentence embeddings + DBSCAN."""

    def __init__(self):
        self._model = None

    def setup(self):
        """Load sentence-transformers model."""
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(ProductIdeationConfig.EMBEDDING_MODEL)
            log.info("Sentence-transformers model loaded: %s", ProductIdeationConfig.EMBEDDING_MODEL)
        except ImportError:
            log.warning("sentence-transformers not available — question clustering disabled")
            self._model = None

    def cluster(self, questions: list[str]) -> list[QuestionCluster]:
        """Cluster similar questions and return sorted by frequency."""
        if not self._model or len(questions) < ProductIdeationConfig.CLUSTER_MIN_SAMPLES:
            return []

        from sklearn.cluster import DBSCAN
        from sklearn.metrics.pairwise import cosine_distances

        embeddings = self._model.encode(questions)
        distances = cosine_distances(embeddings)

        clustering = DBSCAN(
            eps=ProductIdeationConfig.CLUSTER_EPS,
            min_samples=ProductIdeationConfig.CLUSTER_MIN_SAMPLES,
            metric="precomputed",
        )
        labels = clustering.fit_predict(distances)

        # Group questions by cluster
        clusters_dict: dict[int, list[str]] = {}
        for idx, label in enumerate(labels):
            if label == -1:
                continue
            clusters_dict.setdefault(label, []).append(questions[idx])

        # Build QuestionCluster objects
        result = []
        for cluster_questions in sorted(clusters_dict.values(), key=len, reverse=True):
            result.append(QuestionCluster(
                representative=cluster_questions[0],
                questions=cluster_questions,
                frequency=len(cluster_questions),
            ))

        return result

    def teardown(self):
        self._model = None
