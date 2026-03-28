"""
Normalization pipeline orchestrator.

Chains the four processors in sequence:
  1. Language detection (fastText)
  2. Named Entity Recognition (spaCy)
  3. Near-duplicate detection (SimHash)
  4. PII scrubbing (Presidio)

If any processor raises, the document is routed to the dead letter topic.

All processor models are lazy-loaded on first access to reduce startup time
and memory usage. NER and PII share a single spaCy model instance (~800MB saved).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from normalization.config import NormalizationConfig
from normalization.processors.base import BaseProcessor
from schemas.document import OsintDocument

log = logging.getLogger("normalization.pipeline")


@dataclass
class ProcessingResult:
    doc: OsintDocument
    success: bool
    error: Optional[str] = None
    failed_processor: Optional[str] = None


class NormalizationPipeline:
    """Orchestrates the sequence of normalization processors."""

    def __init__(self):
        self._language_detector = None
        self._ner = None
        self._dedup = None
        self._pii = None
        self._shared_spacy_nlp = None
        self._lang_failed = False
        self._pii_failed = False

    def _get_shared_spacy(self):
        """Load one spaCy model shared by NER and PII."""
        if self._shared_spacy_nlp is None:
            import spacy

            self._shared_spacy_nlp = spacy.load(
                NormalizationConfig.SPACY_MODEL,
                disable=["parser", "lemmatizer", "textcat", "tagger"],
            )
            log.info("Shared spaCy model '%s' loaded", NormalizationConfig.SPACY_MODEL)
        return self._shared_spacy_nlp

    @property
    def language_detector(self) -> Optional[BaseProcessor]:
        if self._language_detector is None and not self._lang_failed:
            try:
                from normalization.processors.language import LanguageDetector

                det = LanguageDetector(NormalizationConfig.FASTTEXT_MODEL_PATH)
                det.setup()
                self._language_detector = det
            except Exception as e:
                log.warning("Language detector unavailable, skipping: %s", e)
                self._lang_failed = True
        return self._language_detector

    @property
    def ner(self) -> BaseProcessor:
        if self._ner is None:
            from normalization.processors.ner import EntityExtractor

            nlp = self._get_shared_spacy()
            ner = EntityExtractor(NormalizationConfig.SPACY_MODEL, nlp=nlp)
            ner.setup()
            self._ner = ner
        return self._ner

    @property
    def dedup(self) -> BaseProcessor:
        if self._dedup is None:
            from normalization.processors.dedup import SimHashDeduplicator

            dedup = SimHashDeduplicator()
            dedup.setup()
            self._dedup = dedup
        return self._dedup

    @property
    def pii(self) -> Optional[BaseProcessor]:
        if self._pii is None and not self._pii_failed:
            try:
                from normalization.processors.pii import PIIScrubber

                nlp = self._get_shared_spacy()
                pii = PIIScrubber(spacy_nlp=nlp)
                pii.setup()
                self._pii = pii
            except Exception as e:
                log.warning("PII scrubber unavailable, skipping: %s", e)
                self._pii_failed = True
        return self._pii

    def setup(self) -> None:
        """Pre-warm all processors (optional — they lazy-load on first use)."""
        log.info("Setting up normalization pipeline...")
        _ = self.language_detector
        _ = self.ner
        _ = self.dedup
        _ = self.pii
        count = sum(1 for p in [self._language_detector, self._ner, self._dedup, self._pii] if p)
        log.info("Pipeline ready — %d processors loaded", count)

    def process(self, doc: OsintDocument) -> ProcessingResult:
        """Run a document through all processors in sequence."""
        processors: list[tuple[str, Optional[BaseProcessor]]] = [
            ("language", self.language_detector),
            ("ner", self.ner),
            ("dedup", self.dedup),
            ("pii", self.pii),
        ]

        for name, processor in processors:
            if processor is None:
                continue
            try:
                doc = processor.process(doc)
            except Exception as e:
                log.error("Processor '%s' failed on doc %s: %s", name, doc.doc_id, e)
                doc.metadata["error"] = str(e)
                doc.metadata["failed_processor"] = name
                return ProcessingResult(
                    doc=doc, success=False, error=str(e), failed_processor=name,
                )

        doc.metadata["normalized_at"] = datetime.now(timezone.utc).isoformat()
        return ProcessingResult(doc=doc, success=True)

    def teardown(self) -> None:
        """Cleanup all processors."""
        for name, proc in [
            ("language", self._language_detector),
            ("ner", self._ner),
            ("dedup", self._dedup),
            ("pii", self._pii),
        ]:
            if proc is None:
                continue
            try:
                proc.teardown()
            except Exception as e:
                log.warning("Teardown error for '%s': %s", name, e)
