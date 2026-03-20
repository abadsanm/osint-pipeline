"""
Normalization pipeline orchestrator.

Chains the four processors in sequence:
  1. Language detection (fastText)
  2. Named Entity Recognition (spaCy)
  3. Near-duplicate detection (SimHash)
  4. PII scrubbing (Presidio)

If any processor raises, the document is routed to the dead letter topic.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from normalization.config import NormalizationConfig
from normalization.processors.base import BaseProcessor
from normalization.processors.language import LanguageDetector
from normalization.processors.ner import EntityExtractor
from normalization.processors.dedup import SimHashDeduplicator
from normalization.processors.pii import PIIScrubber
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
        self._processors: list[tuple[str, BaseProcessor]] = []

    def setup(self) -> None:
        """Initialize all processors. Loads models (~30s)."""
        log.info("Setting up normalization pipeline...")

        # 1. Language detection
        lang_detector = LanguageDetector(NormalizationConfig.FASTTEXT_MODEL_PATH)
        lang_detector.setup()
        self._processors.append(("language", lang_detector))

        # 2. NER
        ner = EntityExtractor(NormalizationConfig.SPACY_MODEL)
        ner.setup()
        self._processors.append(("ner", ner))

        # 3. Dedup
        dedup = SimHashDeduplicator()
        dedup.setup()
        self._processors.append(("dedup", dedup))

        # 4. PII — reuse spaCy NLP from NER to avoid loading it twice
        pii = PIIScrubber(spacy_nlp=ner.nlp)
        pii.setup()
        self._processors.append(("pii", pii))

        log.info("Pipeline ready — %d processors loaded", len(self._processors))

    def process(self, doc: OsintDocument) -> ProcessingResult:
        """Run a document through all processors in sequence."""
        for name, processor in self._processors:
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
        for name, processor in self._processors:
            try:
                processor.teardown()
            except Exception as e:
                log.warning("Teardown error for '%s': %s", name, e)
