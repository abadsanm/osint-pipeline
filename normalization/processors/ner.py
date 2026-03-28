"""
Named Entity Recognition using spaCy.

Extracts tickers, company names, people, and products from content_text.
Populates doc.entities with EntityMention instances.

Only processes English text (doc.language == "en") since the spaCy model
is English-only. Non-English documents pass through with entities unchanged.
"""

import logging
import re

from normalization.config import NormalizationConfig
from normalization.processors.base import BaseProcessor
from schemas.document import EntityMention, OsintDocument

log = logging.getLogger("normalization.ner")

# spaCy label → pipeline entity_type mapping
LABEL_MAP = {
    "ORG": "COMPANY",
    "PERSON": "PERSON",
    "PRODUCT": "PRODUCT",
    "GPE": "LOCATION",
    "FAC": "LOCATION",
    "NORP": "GROUP",
    "LAW": "REGULATION",
}

# Labels to skip (too noisy for our purposes)
SKIP_LABELS = {"MONEY", "CARDINAL", "ORDINAL", "QUANTITY", "PERCENT", "DATE", "TIME"}

# Common ticker patterns
TICKER_RE = re.compile(r"\$([A-Z]{1,5})\b")

# Heuristic: all-caps 1-5 letter tokens that appear frequently as tickers
TICKER_LIKE_RE = re.compile(r"\b([A-Z]{2,5})\b")

# Known common words that look like tickers but aren't
TICKER_BLACKLIST = {
    "CEO", "CFO", "CTO", "COO", "IPO", "SEC", "NYSE", "GDP", "ETF", "FBI",
    "CIA", "NASA", "HTTP", "HTML", "JSON", "API", "USA", "USD", "EUR", "GBP",
    "THE", "AND", "FOR", "NOT", "ARE", "BUT", "WAS", "HAS", "ALL", "ANY",
    "NEW", "NOW", "OLD", "OUR", "YOU", "HER", "HIS", "WHO", "HOW", "WHY",
    "CAN", "MAY", "LET", "SAY", "GET", "GOT", "PUT", "RUN", "SET",
    "AI", "ML", "IT", "OR", "IF", "IS", "NO", "SO", "UP", "DO", "GO",
    "DD", "WSB", "IMO", "YOLO", "HODL", "FUD", "FOMO", "ATH", "EPS",
    "PE", "PS", "PB", "ROE", "ROI", "YOY", "QOQ", "MOM", "TTM",
}


class EntityExtractor(BaseProcessor):

    def __init__(self, model_name: str, nlp=None):
        self._model_name = model_name
        self._nlp = nlp

    def setup(self) -> None:
        if self._nlp is not None:
            log.info("spaCy model reused (shared instance)")
            return
        import spacy

        self._nlp = spacy.load(
            self._model_name,
            disable=["parser", "lemmatizer", "textcat", "tagger"],
        )
        log.info("spaCy model '%s' loaded", self._model_name)

    @property
    def nlp(self):
        """Expose the spaCy NLP object for reuse (e.g., by Presidio)."""
        return self._nlp

    def process(self, doc: OsintDocument) -> OsintDocument:
        if not self._nlp or not doc.content_text:
            return doc

        # Only run NER on English text
        if doc.language and doc.language != "en":
            return doc

        text = doc.content_text

        # Extract ticker symbols from $AAPL patterns first
        entities = []
        for match in TICKER_RE.finditer(text):
            ticker = match.group(1)
            entities.append(EntityMention(
                text=ticker,
                entity_type="TICKER",
                confidence=0.95,
            ))

        # Run spaCy NER (single doc — for batch, use process_batch)
        spacy_doc = self._nlp(text[:100000])  # Cap at 100k chars for safety

        seen_texts = {e.text.upper() for e in entities}  # Avoid duplicates

        for ent in spacy_doc.ents:
            if ent.label_ in SKIP_LABELS:
                continue

            entity_type = LABEL_MAP.get(ent.label_)
            if not entity_type:
                continue

            ent_text = ent.text.strip()
            if not ent_text or len(ent_text) < 2:
                continue

            # Skip overly long entity text (likely sentence fragments, not entities)
            if len(ent_text) > 60:
                continue

            # Check if an ORG entity might be a ticker
            if ent.label_ == "ORG" and ent_text.upper() not in seen_texts:
                upper = ent_text.upper()
                if (
                    TICKER_LIKE_RE.fullmatch(upper)
                    and upper not in TICKER_BLACKLIST
                    and len(upper) <= 5
                ):
                    entities.append(EntityMention(
                        text=upper,
                        entity_type="TICKER",
                        confidence=0.7,
                    ))
                    seen_texts.add(upper)
                    continue

            if ent_text.upper() in seen_texts:
                continue
            seen_texts.add(ent_text.upper())

            # Clean entity text: strip trailing punctuation and possessives
            ent_text = ent_text.rstrip(".,;:!?'\"")
            if ent_text.endswith("'s"):
                ent_text = ent_text[:-2]
            ent_text = ent_text.strip()
            if not ent_text or len(ent_text) < 2:
                continue

            entities.append(EntityMention(
                text=ent_text,
                entity_type=entity_type,
                confidence=round(0.85, 2),  # spaCy doesn't expose per-entity scores
            ))

        doc.entities = entities
        return doc

    def process_batch(self, docs: list[OsintDocument]) -> list[OsintDocument]:
        """Process multiple documents using nlp.pipe() for better throughput."""
        if not self._nlp:
            return docs

        # Separate English docs (NER-eligible) from others (pass-through)
        eligible = []
        indices = []
        for i, doc in enumerate(docs):
            if doc.content_text and (not doc.language or doc.language == "en"):
                eligible.append(doc)
                indices.append(i)

        if not eligible:
            return docs

        texts = [d.content_text[:100000] for d in eligible]
        spacy_docs = list(self._nlp.pipe(texts, batch_size=32))

        for doc, spacy_doc in zip(eligible, spacy_docs):
            entities = []
            for match in TICKER_RE.finditer(doc.content_text):
                ticker = match.group(1)
                entities.append(EntityMention(
                    text=ticker, entity_type="TICKER", confidence=0.95,
                ))

            seen_texts = {e.text.upper() for e in entities}

            for ent in spacy_doc.ents:
                if ent.label_ in SKIP_LABELS:
                    continue
                entity_type = LABEL_MAP.get(ent.label_)
                if not entity_type:
                    continue
                ent_text = ent.text.strip()
                if not ent_text or len(ent_text) < 2 or len(ent_text) > 60:
                    continue
                if ent.label_ == "ORG" and ent_text.upper() not in seen_texts:
                    upper = ent_text.upper()
                    if (
                        TICKER_LIKE_RE.fullmatch(upper)
                        and upper not in TICKER_BLACKLIST
                        and len(upper) <= 5
                    ):
                        entities.append(EntityMention(
                            text=upper, entity_type="TICKER", confidence=0.7,
                        ))
                        seen_texts.add(upper)
                        continue
                if ent_text.upper() in seen_texts:
                    continue
                seen_texts.add(ent_text.upper())
                ent_text = ent_text.rstrip(".,;:!?'\"")
                if ent_text.endswith("'s"):
                    ent_text = ent_text[:-2]
                ent_text = ent_text.strip()
                if not ent_text or len(ent_text) < 2:
                    continue
                entities.append(EntityMention(
                    text=ent_text, entity_type=entity_type, confidence=0.85,
                ))

            doc.entities = entities

        return docs
