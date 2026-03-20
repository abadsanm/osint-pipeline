"""
PII scrubbing using Microsoft Presidio.

Detects and replaces PII in content_text with typed placeholders
(e.g., <EMAIL_ADDRESS>, <PHONE_NUMBER>). Runs LAST in the pipeline
so that NER can extract entities from the original text first.

Only replaces PII for non-public-figure entities. Public figures
(politicians in SEC filings, CEOs) are preserved since they are
part of the OSINT signal.
"""

import logging
from typing import Optional

from normalization.config import NormalizationConfig
from normalization.processors.base import BaseProcessor
from schemas.document import OsintDocument

log = logging.getLogger("normalization.pii")


class PIIScrubber(BaseProcessor):

    def __init__(
        self,
        entities: Optional[list[str]] = None,
        spacy_nlp=None,
    ):
        self._entity_types = entities or NormalizationConfig.PRESIDIO_ENTITIES
        self._spacy_nlp = spacy_nlp
        self._analyzer = None
        self._anonymizer = None

    def setup(self) -> None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import SpacyNlpEngine, NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine
        from presidio_anonymizer.entities import OperatorConfig

        # Reuse spaCy model if provided (avoids loading it twice)
        if self._spacy_nlp:
            nlp_engine = SpacyNlpEngine(nlp={"en": self._spacy_nlp})
            self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        else:
            self._analyzer = AnalyzerEngine()

        self._anonymizer = AnonymizerEngine()
        self._operator_config = {
            entity: OperatorConfig("replace", {"new_value": f"<{entity}>"})
            for entity in self._entity_types
        }

        log.info("Presidio initialized — detecting: %s", self._entity_types)

    def process(self, doc: OsintDocument) -> OsintDocument:
        if not self._analyzer or not doc.content_text:
            return doc

        # Determine language for analysis (default to English)
        lang = doc.language if doc.language in ("en", "es", "de", "fr", "it") else "en"

        try:
            # Detect PII
            results = self._analyzer.analyze(
                text=doc.content_text,
                entities=self._entity_types,
                language=lang,
            )

            if results:
                # Anonymize
                anonymized = self._anonymizer.anonymize(
                    text=doc.content_text,
                    analyzer_results=results,
                    operators=self._operator_config,
                )
                doc.content_text = anonymized.text

                pii_types = list({r.entity_type for r in results})
                doc.metadata["pii_detected_count"] = len(results)
                doc.metadata["pii_types_found"] = pii_types
            else:
                doc.metadata["pii_detected_count"] = 0
                doc.metadata["pii_types_found"] = []

        except Exception as e:
            log.warning("Presidio processing error: %s", e)
            doc.metadata["pii_error"] = str(e)

        return doc
