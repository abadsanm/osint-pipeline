"""
Feature request extraction via pattern mining.

Identifies "I wish it could...", "it should...", "missing..." and similar
patterns in review text to surface unmet consumer needs.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

log = logging.getLogger("product_ideation.feature_requests")


@dataclass
class FeatureRequest:
    """An extracted feature request from review text."""
    matched_text: str
    extracted_need: str
    pattern_type: str   # "wish", "want", "should", "missing", "disappointed", "why_cant", "would_be"
    source_text: str


# Compiled regex patterns for feature request extraction
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("wish", re.compile(
        r"(?i)\bi\s+wish\s+(?:it|this|they|the\s+\w+)\s+(?:could|would|had|was|were|did)\s+(.{10,120})",
    )),
    ("want", re.compile(
        r"(?i)\bi\s+(?:really\s+)?(?:want|need|would\s+love|would\s+like)\s+(.{10,120})",
    )),
    ("should", re.compile(
        r"(?i)(?:it|this|they)\s+(?:should|must|need\s+to|ought\s+to)\s+(.{10,120})",
    )),
    ("missing", re.compile(
        r"(?i)(?:missing|lacks?|no\s+(?:option|way|ability|feature|support)\s+(?:to|for))\s+(.{10,120})",
    )),
    ("if_only", re.compile(
        r"(?i)if\s+only\s+(?:it|this|they)\s+(.{10,120})",
    )),
    ("disappointed", re.compile(
        r"(?i)disappointed\s+(?:that|because)\s+(.{10,120})",
    )),
    ("why_cant", re.compile(
        r"(?i)why\s+(?:can'?t|doesn'?t|don'?t|won'?t|isn'?t)\s+(?:it|this|they)\s+(.{10,120})",
    )),
    ("would_be", re.compile(
        r"(?i)(?:it\s+)?would\s+be\s+(?:nice|great|better|helpful|good)\s+(?:if|to)\s+(.{10,120})",
    )),
    ("expected", re.compile(
        r"(?i)i\s+expected\s+(.{10,80})\s+but",
    )),
]


def extract_feature_requests(text: str) -> list[FeatureRequest]:
    """Extract feature requests from review text using pattern matching.

    Returns a list of FeatureRequest objects.
    """
    if not text:
        return []

    results = []
    seen = set()

    for pattern_type, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            matched = match.group(0).strip()
            need = match.group(1).strip().rstrip(".!,;")

            # Deduplicate overlapping matches
            key = need.lower()[:50]
            if key in seen:
                continue
            seen.add(key)

            results.append(FeatureRequest(
                matched_text=matched,
                extracted_need=need,
                pattern_type=pattern_type,
                source_text=text[:200],
            ))

    return results
