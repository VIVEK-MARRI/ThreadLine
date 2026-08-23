"""Meeting repository abstraction and in-memory implementation.

The abstract base class defines the contract that any storage backend must
satisfy.  Today we ship InMemoryMeetingRepository, which is sufficient for
development and testing.  When we add PostgreSQL, we implement the same
abstract interface without touching the service layer.
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.models.meeting import Meeting


class AbstractMeetingRepository(ABC):
    """Storage contract for meetings.

    All methods are intentionally synchronous for now.  When we introduce
    async persistence (e.g. asyncpg), this interface will be updated once
    and all callers will follow.
    """

    @abstractmethod
    def save(self, meeting: Meeting) -> None:
        """Persist a meeting record."""
        ...

    @abstractmethod
    def get_by_id(self, meeting_id: str) -> Optional[Meeting]:
        """Retrieve a meeting by its unique identifier.

        Returns None if no meeting with the given ID exists.
        """
        ...


class InMemoryMeetingRepository(AbstractMeetingRepository):
    """Thread-unsafe in-memory store, suitable for development and testing.

    For production use, replace this with a persistent backend that
    implements AbstractMeetingRepository.
    """

    def __init__(self) -> None:
        self._store: dict[str, Meeting] = {}

    def save(self, meeting: Meeting) -> None:
        """Store a meeting in the in-memory dictionary."""
        self._store[meeting.meeting_id] = meeting

    def get_by_id(self, meeting_id: str) -> Optional[Meeting]:
        """Return the meeting with the given ID, or None."""
        return self._store.get(meeting_id)
