"""
Base processor interface for the normalization pipeline.
"""

from abc import ABC, abstractmethod

from schemas.document import OsintDocument


class BaseProcessor(ABC):
    """All normalization processors implement this interface."""

    @abstractmethod
    def setup(self) -> None:
        """Load models, initialize engines. Called once at startup."""

    @abstractmethod
    def process(self, doc: OsintDocument) -> OsintDocument:
        """Process a document. May mutate and return it."""

    def teardown(self) -> None:
        """Cleanup. Override if needed."""
