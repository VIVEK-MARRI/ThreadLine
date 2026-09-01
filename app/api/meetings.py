"""Meetings API router.

Handles HTTP concerns only: routing, request parsing, response serialisation,
and HTTP error translation.  All business logic lives in MeetingService or
ExtractionService.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.extraction.base import (
    ExtractionError,
    ExtractionProviderNotConfiguredError,
    ExtractionProviderResponseError,
)
from app.models.meeting import Meeting
from app.repositories.extraction_repository import (
    AbstractExtractionRepository,
    InMemoryExtractionRepository,
)
from app.repositories.meeting_repository import (
    AbstractMeetingRepository,
    InMemoryMeetingRepository,
)
from app.schemas.extraction import (
    DecisionSchema,
    EvidenceSchema,
    ExtractionResponse,
    IssueSchema,
    RiskSchema,
    TaskSchema,
)
from app.schemas.meeting import (
    MeetingIngestRequest,
    MeetingIngestResponse,
    MeetingResponse,
)
from app.services.extraction_service import ExtractionService, MeetingNotFoundError
from app.services.meeting_service import MeetingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meetings", tags=["Meetings"])

# ---------------------------------------------------------------------------
# Shared repository singletons
# (When we move to PostgreSQL we'll replace these with session-scoped factories.)
# ---------------------------------------------------------------------------
_meeting_repository: AbstractMeetingRepository = InMemoryMeetingRepository()
_extraction_repository: AbstractExtractionRepository = InMemoryExtractionRepository()


def get_meeting_repository() -> AbstractMeetingRepository:
    """Return the shared MeetingRepository singleton.

    Exported so other routers (e.g. entities/correlation) can share the
    same instance and see meetings ingested via this router.  This avoids
    duplicating the singleton and keeps all meeting data in one store.
    """
    return _meeting_repository


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def _build_extraction_provider():
    """Instantiate the extraction provider configured via EXTRACTION_PROVIDER."""
    provider_name = settings.extraction_provider.lower()

    if provider_name == "openai":
        from app.extraction.openai_provider import OpenAIExtractionProvider
        return OpenAIExtractionProvider()

    if provider_name == "fake":
        # Smoke-test mode: return an empty-but-valid extraction result.
        from datetime import datetime, timezone

        from app.extraction.fake_provider import FakeExtractionProvider
        from app.models.extraction import ExtractionResult

        empty_result = ExtractionResult(
            meeting_id="__placeholder__",
            extracted_at=datetime.now(tz=timezone.utc),
        )
        return FakeExtractionProvider(result=empty_result)

    raise ValueError(
        f"Unknown EXTRACTION_PROVIDER value: '{settings.extraction_provider}'.  "
        "Supported values: 'openai', 'fake'."
    )


_extraction_provider = _build_extraction_provider()


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------

def get_meeting_service() -> MeetingService:
    """FastAPI dependency that provides a configured MeetingService."""
    return MeetingService(repository=_meeting_repository)


def get_extraction_service() -> ExtractionService:
    """FastAPI dependency that provides a configured ExtractionService."""
    return ExtractionService(
        meeting_repository=_meeting_repository,
        extraction_repository=_extraction_repository,
        provider=_extraction_provider,
    )


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


def _extraction_to_response(result) -> ExtractionResponse:
    """Translate an ExtractionResult domain model to the API response schema."""
    return ExtractionResponse(
        meeting_id=result.meeting_id,
        extracted_at=result.extracted_at,
        issues=[
            IssueSchema(
                description=i.description,
                evidence=EvidenceSchema(source_text=i.evidence.source_text),
            )
            for i in result.issues
        ],
        tasks=[
            TaskSchema(
                description=t.description,
                owner=t.owner,
                deadline=t.deadline,
                evidence=EvidenceSchema(source_text=t.evidence.source_text),
            )
            for t in result.tasks
        ],
        decisions=[
            DecisionSchema(
                description=d.description,
                evidence=EvidenceSchema(source_text=d.evidence.source_text),
            )
            for d in result.decisions
        ],
        risks=[
            RiskSchema(
                description=r.description,
                severity=r.severity,
                evidence=EvidenceSchema(source_text=r.evidence.source_text),
            )
            for r in result.risks
        ],
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


@router.post(
    "/{meeting_id}/extract",
    response_model=ExtractionResponse,
    status_code=status.HTTP_200_OK,
    summary="Extract structured facts from a meeting transcript",
    description=(
        "Runs the information extraction pipeline on a stored meeting transcript.  "
        "Returns evidence-backed issues, tasks, decisions, and risks.  "
        "Calling this endpoint multiple times will overwrite the previous result."
    ),
)
def extract_meeting(
    meeting_id: str,
    service: ExtractionService = Depends(get_extraction_service),
) -> ExtractionResponse:
    """Trigger extraction for a meeting and return structured facts."""
    try:
        result = service.extract_meeting(meeting_id)
    except MeetingNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ExtractionProviderNotConfiguredError as exc:
        logger.error("Extraction provider not configured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The extraction provider is not configured.  "
                "Check that OPENAI_API_KEY is set in your environment."
            ),
        ) from exc
    except ExtractionProviderResponseError as exc:
        logger.error("Extraction provider returned invalid response: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The extraction provider returned a response that could not be "
                "validated.  This is a temporary issue — please try again."
            ),
        ) from exc
    except ExtractionError as exc:
        logger.error("Extraction failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The extraction service is temporarily unavailable.  "
                "Please try again later."
            ),
        ) from exc

    return _extraction_to_response(result)

