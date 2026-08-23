"""Meetings API router.

Handles HTTP concerns only: routing, request parsing, response serialisation,
and HTTP error translation.  All business logic lives in MeetingService.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.meeting import Meeting
from app.repositories.meeting_repository import (
    AbstractMeetingRepository,
    InMemoryMeetingRepository,
)
from app.schemas.meeting import (
    MeetingIngestRequest,
    MeetingIngestResponse,
    MeetingResponse,
)
from app.services.meeting_service import MeetingService

router = APIRouter(prefix="/meetings", tags=["Meetings"])

# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------
# A single shared repository instance is used for the lifetime of the process.
# When we move to PostgreSQL, we'll replace this with a session-scoped factory.
_repository: AbstractMeetingRepository = InMemoryMeetingRepository()


def get_meeting_service() -> MeetingService:
    """FastAPI dependency that provides a configured MeetingService."""
    return MeetingService(repository=_repository)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _meeting_to_response(meeting: Meeting) -> MeetingResponse:
    """Translate the internal Meeting domain model to the API response schema."""
    return MeetingResponse(
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        transcript=meeting.transcript,
        meeting_date=meeting.meeting_date,
        participants=meeting.participants,
        ingested_at=meeting.ingested_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=MeetingIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a meeting transcript",
    description=(
        "Submit a meeting transcript for ingestion into Threadline. "
        "Returns a unique meeting ID and ingestion status."
    ),
)
def ingest_meeting(
    request: MeetingIngestRequest,
    service: MeetingService = Depends(get_meeting_service),
) -> MeetingIngestResponse:
    """Ingest a meeting and return its assigned ID."""
    meeting = service.ingest_meeting(request)
    return MeetingIngestResponse(meeting_id=meeting.meeting_id, status="ingested")


@router.get(
    "/{meeting_id}",
    response_model=MeetingResponse,
    summary="Retrieve a meeting by ID",
    description="Fetch the full stored record for a previously ingested meeting.",
)
def get_meeting(
    meeting_id: str,
    service: MeetingService = Depends(get_meeting_service),
) -> MeetingResponse:
    """Return a meeting by its ID or raise HTTP 404."""
    meeting = service.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting '{meeting_id}' not found.",
        )
    return _meeting_to_response(meeting)
