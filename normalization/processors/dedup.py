"""
Near-duplicate detection using SimHash.

Computes a 64-bit SimHash fingerprint for each document and checks against
a sliding window index. Documents within a configurable hamming distance
are flagged as near-duplicates.

The document still flows through the pipeline — downstream consumers
decide whether to filter near-duplicates.
"""

import hashlib
import logging
import time
from collections import OrderedDict
from typing import Optional

from normalization.config import NormalizationConfig
from normalization.processors.base import BaseProcessor
from schemas.document import OsintDocument

log = logging.getLogger("normalization.dedup")


def _tokenize(text: str, shingle_size: int = 3) -> list[str]:
    """Split text into word-level shingles."""
    words = text.lower().split()
    if len(words) < shingle_size:
        return [" ".join(words)] if words else []
    return [
        " ".join(words[i : i + shingle_size])
        for i in range(len(words) - shingle_size + 1)
    ]


def _hash_token(token: str) -> int:
    """Hash a token to a 64-bit integer."""
    h = hashlib.md5(token.encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def compute_simhash(text: str, shingle_size: int = 3) -> int:
    """Compute a 64-bit SimHash fingerprint for the given text."""
    tokens = _tokenize(text, shingle_size)
    if not tokens:
        return 0

    # Initialize bit counters
    bit_counts = [0] * 64

    for token in tokens:
        h = _hash_token(token)
        for i in range(64):
            if h & (1 << i):
                bit_counts[i] += 1
            else:
                bit_counts[i] -= 1

    # Build fingerprint from sign of each bit counter
    fingerprint = 0
    for i in range(64):
        if bit_counts[i] > 0:
            fingerprint |= (1 << i)

    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """Count differing bits between two 64-bit integers."""
    return bin(a ^ b).count("1")


class SimHashDeduplicator(BaseProcessor):

    def __init__(
        self,
        threshold: int = NormalizationConfig.SIMHASH_THRESHOLD,
        max_index_size: int = NormalizationConfig.SIMHASH_INDEX_SIZE,
    ):
        self._threshold = threshold
        self._max_index_size = max_index_size
        # OrderedDict for LRU-style eviction: {simhash: (doc_id, timestamp)}
        self._index: OrderedDict[int, tuple[str, float]] = OrderedDict()

    def setup(self) -> None:
        log.info(
            "SimHash deduplicator initialized — threshold=%d, max_index=%d",
            self._threshold, self._max_index_size,
        )

    def process(self, doc: OsintDocument) -> OsintDocument:
        if not doc.content_text:
            return doc

        # Compute exact-duplicate hash first (cheap)
        doc.compute_dedup_hash()

        # Compute SimHash fingerprint
        fingerprint = compute_simhash(doc.content_text)
        doc.dedup_hash = f"{fingerprint:016x}"

        # Check for near-duplicates
        duplicate_of = self._find_near_duplicate(fingerprint)
        if duplicate_of:
            doc.metadata["is_near_duplicate"] = True
            doc.metadata["duplicate_of"] = duplicate_of
        else:
            doc.metadata["is_near_duplicate"] = False

        # Add to index
        self._index[fingerprint] = (doc.doc_id, time.time())
        self._index.move_to_end(fingerprint)

        # Evict oldest entries if over limit
        while len(self._index) > self._max_index_size:
            self._index.popitem(last=False)

        return doc

    def _find_near_duplicate(self, fingerprint: int) -> Optional[str]:
        """Search the index for a near-duplicate within hamming threshold."""
        for existing_hash, (doc_id, _ts) in self._index.items():
            if hamming_distance(fingerprint, existing_hash) <= self._threshold:
                return doc_id
        return None
