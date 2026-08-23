"""Meeting service layer.

Orchestrates business logic for meeting ingestion and retrieval.
The service knows about domain models and the repository interface but
has no awareness of HTTP, Pydantic schemas, or storage mechanics.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.models.meeting import Meeting
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.schemas.meeting import MeetingIngestRequest


class MeetingService:
    """Encapsulates all meeting-related business operations."""

    def __init__(self, repository: AbstractMeetingRepository) -> None:
        self._repository = repository

    def ingest_meeting(self, request: MeetingIngestRequest) -> Meeting:
        """Create a new Meeting record from a client request and persist it.

        Steps:
        1. Generate a unique meeting ID.
        2. Build the internal domain model.
        3. Record the ingestion timestamp (UTC).
        4. Persist via the repository.
        5. Return the saved domain model.
        """
        meeting = Meeting(
            meeting_id=str(uuid.uuid4()),
            title=request.title,
            transcript=request.transcript,
            meeting_date=request.meeting_date,
            participants=request.participants or [],
            ingested_at=datetime.now(tz=timezone.utc),
        )
        self._repository.save(meeting)
        return meeting

    def get_meeting(self, meeting_id: str) -> Optional[Meeting]:
        """Return a meeting by ID, or None if it does not exist."""
        return self._repository.get_by_id(meeting_id)
