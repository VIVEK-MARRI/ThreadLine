"""Meetings API router.

Handles HTTP concerns only: routing, request parsing, response serialisation,
and HTTP error translation.  All business logic lives in MeetingService or
ExtractionService.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from app.models.background_job import BackgroundJobType

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
from app.persistence.sqlite_store import SQLiteSourceStore
from app.persistence.source_backend import build_sqlite_source_store
from app.repositories.sqlite_source_repositories import (
    SQLiteExtractionRepository,
    SQLiteMeetingRepository,
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
from app.services.meeting_service import MeetingConflictError, MeetingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meetings", tags=["Meetings"])

# ---------------------------------------------------------------------------
# Shared repository singletons
# (When we move to PostgreSQL we'll replace these with session-scoped factories.)
# ---------------------------------------------------------------------------
_source_store: SQLiteSourceStore | None = None
if settings.source_repository_backend.lower() == "database":
    _source_store = build_sqlite_source_store()
    _meeting_repository: AbstractMeetingRepository = SQLiteMeetingRepository(_source_store)
    _extraction_repository: AbstractExtractionRepository = SQLiteExtractionRepository(_source_store)
else:
    _meeting_repository = InMemoryMeetingRepository()
    _extraction_repository = InMemoryExtractionRepository()


def get_meeting_repository() -> AbstractMeetingRepository:
    """Return the shared MeetingRepository singleton.

    Exported so other routers (e.g. entities/correlation) can share the
    same instance and see meetings ingested via this router.  This avoids
    duplicating the singleton and keeps all meeting data in one store.
    """
    return _meeting_repository


def get_source_store() -> SQLiteSourceStore | None:
    """Return the shared durable store when database mode is enabled."""
    return _source_store


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
            meeting_id="",
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
    def persist_ingestion(meeting: Meeting) -> None:
        from app.api.jobs import get_job_scheduler
        from app.services.processing_consistency_service import processing_revision_for

        scheduler = get_job_scheduler()
        job = scheduler.build_job(
            BackgroundJobType.MEETING_PROCESSING,
            meeting.meeting_id,
            processing_revision=processing_revision_for(meeting.source_revision),
        )
        if isinstance(_meeting_repository, SQLiteMeetingRepository):
            _meeting_repository.save_and_enqueue(meeting, job)
        else:
            _meeting_repository.save(meeting)
            scheduler._repository.enqueue(job)

    return MeetingService(repository=_meeting_repository, ingestion_persister=persist_ingestion)


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
    """Ingest a meeting and return its assigned ID.

    MUTATES SOURCE TRUTH: creates authoritative source revision 1 and its
    durably tied processing revision "1".  Idempotent for identical payloads.
    """
    try:
        meeting = service.ingest_meeting(request)
    except MeetingConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # The service persists the meeting and processing intent together in database mode.
    # This second enqueue is idempotent (same deterministic job_id) and covers
    # in-memory backends where the persister path differs.
    from app.api.jobs import get_job_scheduler
    from app.services.processing_consistency_service import processing_revision_for

    get_job_scheduler().enqueue(
        BackgroundJobType.MEETING_PROCESSING,
        meeting.meeting_id,
        processing_revision=processing_revision_for(meeting.source_revision),
    )
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


@router.put(
    "/{meeting_id}",
    response_model=MeetingIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a new source revision for a meeting",
)
def revise_meeting(
    meeting_id: str,
    request: MeetingIngestRequest,
    service: MeetingService = Depends(get_meeting_service),
) -> MeetingIngestResponse:
    """Persist a changed source and enqueue its distinct processing revision.

    MUTATES SOURCE TRUTH: creates authoritative source revision N+1 and a new
    processing execution tied to N+1.  Historical jobs are preserved; the old
    completed job never blocks the new revision.
    """
    from app.api.jobs import get_job_scheduler
    from app.services.processing_consistency_service import processing_revision_for

    # Concurrent refreshes: exactly one revision ordering is authoritative.
    # If two requests both read N and try to write N+1, the loser gets a
    # MeetingConflictError from the monotonic guard and retries as N+2.
    # Result is always N+1 then N+2, never two ambiguous currents.
    last_conflict: Exception | None = None
    for _ in range(3):
        try:
            meeting = service.revise_meeting(meeting_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except MeetingConflictError as exc:
            last_conflict = exc
            # Re-read current revision and retry (service reads fresh state).
            continue

        scheduler = get_job_scheduler()
        job = scheduler.build_job(
            BackgroundJobType.MEETING_PROCESSING,
            meeting_id,
            processing_revision=processing_revision_for(meeting.source_revision),
        )
        try:
            if isinstance(_meeting_repository, SQLiteMeetingRepository):
                _meeting_repository.save_and_enqueue(meeting, job)
            else:
                _meeting_repository.save(meeting)
                scheduler._repository.enqueue(job)
        except MeetingConflictError as exc:
            last_conflict = exc
            continue
        return MeetingIngestResponse(meeting_id=meeting_id, status="revisioned")
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(last_conflict) if last_conflict else "concurrent revision conflict")


@router.post(
    "/{meeting_id}/extract",
    response_model=ExtractionResponse,
    status_code=status.HTTP_200_OK,
    summary="Refresh extraction and enqueue a revision-tied reprocessing job",
    description=(
        "Runs the information extraction pipeline on a stored meeting transcript.  "
        "Does NOT mutate authoritative meeting source truth (transcript, "
        "participants, source_revision are unchanged); it refreshes derived "
        "extraction state and enqueues a distinct revision-tied reprocessing "
        "job so downstream stages re-run without being blocked by history.  "
        "Calling this endpoint multiple times creates distinct processing "
        "revisions over the same source revision."
    ),
)
def extract_meeting(
    meeting_id: str,
    service: ExtractionService = Depends(get_extraction_service),
) -> ExtractionResponse:
    """Trigger extraction refresh and enqueue its distinct processing revision.

    DOES NOT MUTATE SOURCE TRUTH: meeting transcript/participants/source_revision
    are untouched.  Only derived extraction state is refreshed.
    """
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

    from app.api.jobs import get_job_scheduler
    from app.services.processing_consistency_service import processing_revision_for

    # Distinct refresh revision over the SAME source revision: history is
    # preserved (old completed job never blocks) and the new execution is
    # still explicitly tied to its source revision via the "N@suffix" form.
    get_job_scheduler().enqueue(
        BackgroundJobType.MEETING_PROCESSING,
        meeting_id,
        processing_revision=processing_revision_for(
            result.source_revision, suffix=result.extracted_at.isoformat()
        ),
    )
    return _extraction_to_response(result)

