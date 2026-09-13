"""Meeting service layer.

Orchestrates business logic for meeting ingestion and retrieval.
The service knows about domain models and the repository interface but
has no awareness of HTTP, Pydantic schemas, or storage mechanics.
"""

import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from app.models.meeting import Meeting
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.schemas.meeting import MeetingIngestRequest


class MeetingConflictError(ValueError):
    """A stable meeting ID was reused with a different payload."""


class MeetingService:
    """Encapsulates all meeting-related business operations."""

    def __init__(self, repository: AbstractMeetingRepository, ingestion_persister: Optional[Callable[[Meeting], None]] = None) -> None:
        self._repository = repository
        self._ingestion_persister = ingestion_persister

    def ingest_meeting(self, request: MeetingIngestRequest) -> Meeting:
        """Create a new Meeting record from a client request and persist it.

        Steps:
        1. Generate a unique meeting ID.
        2. Build the internal domain model.
        3. Record the ingestion timestamp (UTC).
        4. Persist via the repository.
        5. Return the saved domain model.
        """
        meeting_id = request.meeting_id or str(uuid.uuid4())
        existing = self._repository.get_by_id(meeting_id)
        if existing is not None:
            if (
                existing.title != request.title
                or existing.transcript != request.transcript
                or existing.meeting_date != request.meeting_date
                or existing.participants != (request.participants or [])
            ):
                raise MeetingConflictError(
                    f"Meeting '{meeting_id}' already exists with a different payload."
                )
            return existing
        meeting = Meeting(
            meeting_id=meeting_id,
            title=request.title,
            transcript=request.transcript,
            meeting_date=request.meeting_date,
            participants=request.participants or [],
            ingested_at=datetime.now(tz=timezone.utc),
            idempotency_key=request.meeting_id,
        )
        if self._ingestion_persister is not None:
            self._ingestion_persister(meeting)
        else:
            self._repository.save(meeting)
        return meeting

    def get_meeting(self, meeting_id: str) -> Optional[Meeting]:
        """Return a meeting by ID, or None if it does not exist."""
        return self._repository.get_by_id(meeting_id)

    def revise_meeting(self, meeting_id: str, request: MeetingIngestRequest) -> Meeting:
        """Create the next authoritative source revision for a meeting."""
        existing = self._repository.get_by_id(meeting_id)
        if existing is None:
            raise KeyError(meeting_id)
        return Meeting(
            meeting_id=meeting_id,
            title=request.title,
            transcript=request.transcript,
            meeting_date=request.meeting_date,
            participants=request.participants or [],
            ingested_at=datetime.now(tz=timezone.utc),
            idempotency_key=existing.idempotency_key,
            source_revision=existing.source_revision + 1,
        )
