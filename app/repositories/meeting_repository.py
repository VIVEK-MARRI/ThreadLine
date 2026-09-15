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

    @abstractmethod
    def list_meetings(
        self,
        organisation_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Meeting]:
        """Return meetings newest first by meeting_date, then meeting_id.

        When organisation_id is given, only that organisation's meetings are
        returned. None preserves the legacy global enumeration. A positive
        limit bounds the returned rows; None means no repository-level bound.
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
        """Store a meeting with monotonic revision guard."""
        existing = self._store.get(meeting.meeting_id)
        if existing is not None:
            if int(meeting.source_revision) < int(existing.source_revision):
                from app.repositories.background_job_repository import StaleJobOwnershipError

                raise StaleJobOwnershipError(
                    f"stale source write rejected for {meeting.meeting_id}: "
                    f"incoming revision {meeting.source_revision} < durable revision {existing.source_revision}"
                )
            if int(meeting.source_revision) == int(existing.source_revision) and meeting.model_dump(mode="json") != existing.model_dump(mode="json"):
                from app.services.meeting_service import MeetingConflictError

                raise MeetingConflictError(
                    f"concurrent source refresh for '{meeting.meeting_id}' at revision {existing.source_revision}; retry as revision {existing.source_revision + 1}"
                )
        self._store[meeting.meeting_id] = meeting

    def get_by_id(self, meeting_id: str) -> Optional[Meeting]:
        """Return the meeting with the given ID, or None."""
        return self._store.get(meeting_id)

    def list_meetings(
        self,
        organisation_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Meeting]:
        """Return meetings newest first, optionally scoped and bounded."""
        meetings = list(self._store.values())
        if organisation_id is not None:
            meetings = [meeting for meeting in meetings if meeting.organisation_id == organisation_id]
        meetings.sort(key=lambda meeting: (-meeting.meeting_date.timestamp(), meeting.meeting_id))
        if limit is not None:
            return meetings[: max(0, limit)]
        return meetings
