"""Extraction result repository abstraction and in-memory implementation.

Follows the exact same pattern as meeting_repository.py:
  - AbstractExtractionRepository defines the storage contract.
  - InMemoryExtractionRepository provides a dev/test implementation.
  - When we move to PostgreSQL, we implement the same abstract interface
    without touching the service layer.

Keyed by meeting_id because each meeting has at most one active extraction
result today (re-extraction overwrites the previous result).
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.models.extraction import ExtractionResult


class AbstractExtractionRepository(ABC):
    """Storage contract for extraction results.

    All methods are intentionally synchronous, matching the meeting
    repository pattern.  When we introduce async persistence, this
    interface will be updated once and all callers will follow.
    """

    @abstractmethod
    def save(self, result: ExtractionResult) -> None:
        """Persist an extraction result, overwriting any previous result
        for the same meeting_id."""
        ...

    @abstractmethod
    def get_by_meeting_id(self, meeting_id: str) -> Optional[ExtractionResult]:
        """Return the most recent extraction result for a meeting, or None."""
        ...


class InMemoryExtractionRepository(AbstractExtractionRepository):
    """Thread-unsafe in-memory store, suitable for development and testing.

    For production use, replace this with a persistent backend that
    implements AbstractExtractionRepository.
    """

    def __init__(self) -> None:
        self._store: dict[str, ExtractionResult] = {}

    def save(self, result: ExtractionResult) -> None:
        """Store (or overwrite) the extraction result for result.meeting_id."""
        self._store[result.meeting_id] = result

    def get_by_meeting_id(self, meeting_id: str) -> Optional[ExtractionResult]:
        """Return the stored result for the given meeting_id, or None."""
        return self._store.get(meeting_id)
