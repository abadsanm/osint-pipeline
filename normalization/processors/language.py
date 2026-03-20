"""
Language detection using fastText lid.176 model.

Sets doc.language to ISO 639-1 code and stores confidence in metadata.
"""

import logging
import os

from normalization.processors.base import BaseProcessor
from schemas.document import OsintDocument

log = logging.getLogger("normalization.language")


class LanguageDetector(BaseProcessor):

    def __init__(self, model_path: str):
        self._model_path = model_path
        self._model = None

    def setup(self) -> None:
        import fasttext

        if not os.path.exists(self._model_path):
            raise FileNotFoundError(
                f"fastText model not found at {self._model_path}. "
                f"Run: python -m normalization.models.download"
            )

        # Suppress fastText warnings about deprecated load_model
        fasttext.FastText.eprint = lambda x: None
        self._model = fasttext.load_model(self._model_path)
        log.info("fastText lid model loaded from %s", self._model_path)

    def process(self, doc: OsintDocument) -> OsintDocument:
        if not self._model or not doc.content_text:
            return doc

        # fastText expects single-line input
        text = doc.content_text.replace("\n", " ").strip()
        if not text:
            return doc

        predictions = self._model.predict(text, k=1)
        # predictions = ([['__label__en']], [array([0.98])])
        labels, confidences = predictions

        if labels and labels[0]:
            lang_code = labels[0][0].replace("__label__", "")
            confidence = float(confidences[0][0]) if confidences[0] else 0.0

            doc.language = lang_code
            doc.metadata["language_confidence"] = round(confidence, 4)

        return doc
