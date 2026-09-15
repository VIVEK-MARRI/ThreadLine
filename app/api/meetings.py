"""Meetings API router.

Handles HTTP concerns only: routing, request parsing, response serialisation,
and HTTP error translation.  All business logic lives in MeetingService or
ExtractionService.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.models.background_job import BackgroundJobType

from app.api.auth import Authorisation, get_request_context, require_permission
from app.auth.models import Permission

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
    MeetingExtractionResponse,
    MeetingIngestRequest,
    MeetingIngestResponse,
    MeetingListResponse,
    MeetingMentionSchema,
    MeetingMentionsResponse,
    MeetingProcessingResponse,
    MeetingProcessingStatusSchema,
    MeetingResponse,
    MeetingSummarySchema,
)
from app.services.extraction_service import ExtractionService, MeetingNotFoundError
from app.services.meeting_service import MeetingConflictError, MeetingService
from app.services.processing_consistency_service import get_consistency_status

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

def get_meeting_service(
    ctx: Authorisation = Depends(get_request_context),
) -> MeetingService:
    """FastAPI dependency that provides a tenant-scoped MeetingService."""
    def persist_ingestion(meeting: Meeting) -> None:
        from app.api.jobs import get_job_scheduler
        from app.services.processing_consistency_service import processing_revision_for

        scheduler = get_job_scheduler()
        job = scheduler.build_job(
            BackgroundJobType.MEETING_PROCESSING,
            meeting.meeting_id,
            processing_revision=processing_revision_for(meeting.source_revision),
            organisation_id=ctx.organisation_id,
        )
        ctx.repos.save_meeting_and_enqueue(meeting, job)

    return MeetingService(repository=ctx.repos.meetings, ingestion_persister=persist_ingestion)


def get_extraction_service(
    ctx: Authorisation = Depends(get_request_context),
) -> ExtractionService:
    """FastAPI dependency that provides a tenant-scoped ExtractionService."""
    return ExtractionService(
        meeting_repository=ctx.repos.meetings,
        extraction_repository=ctx.repos.extractions,
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


def _current_revision_lookup(meeting_repository):
    """Return a tenant-scoped authoritative source-revision lookup.

    Local to this router to avoid a circular import with app.api.entities.
    """

    def _lookup(meeting_id):
        if meeting_id is None:
            return None
        meeting = meeting_repository.get_by_id(meeting_id)
        if meeting is None:
            return None
        try:
            return int(getattr(meeting, "source_revision", 1) or 1)
        except (TypeError, ValueError):
            return None

    return _lookup


def _processing_response(meeting_id, source_revision, consistency) -> MeetingProcessingResponse:
    """Translate durable consistency state to the public processing contract."""
    return MeetingProcessingResponse(
        meeting_id=meeting_id,
        source_revision=int(source_revision or 1),
        status=MeetingProcessingStatusSchema(consistency["status"]),
        processing_complete=bool(consistency["processing_complete"]),
        is_current=bool(consistency["is_current"]),
        extraction_revision=consistency["extraction_revision"],
        derived_revision=consistency["derived_revision"],
        semantic_revision=consistency["semantic_revision"],
        stale_mentions=int(consistency["stale_mentions"] or 0),
        worker_enabled=settings.background_worker_enabled,
    )


def _meeting_consistency(meeting_id, ctx) -> dict:
    """Return tenant-scoped durable processing state for one meeting."""
    return get_consistency_status(
        meeting_id,
        meeting_repository=ctx.repos.meetings,
        job_repository=ctx.repos.jobs,
        extraction_repository=ctx.repos.extractions,
        mention_repository=ctx.repos.mentions,
        semantic_repository=ctx.repos.semantic,
    )


def _meeting_summary(meeting, ctx) -> MeetingSummarySchema:
    """Project one tenant-scoped meeting into its bounded list record."""
    extraction = ctx.repos.extractions.get_by_meeting_id(meeting.meeting_id)
    consistency = _meeting_consistency(meeting.meeting_id, ctx)
    mentions = ctx.repos.mentions.list_current_by_meeting_id(
        meeting.meeting_id, _current_revision_lookup(ctx.repos.meetings)
    )
    resolved_entity_ids = sorted(
        {mention.entity_id for mention in mentions if mention.entity_id is not None}
    )
    return MeetingSummarySchema(
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        meeting_date=meeting.meeting_date,
        participants=meeting.participants,
        ingested_at=meeting.ingested_at,
        source_revision=int(meeting.source_revision or 1),
        processing_status=MeetingProcessingStatusSchema(consistency["status"]),
        extraction_revision=consistency["extraction_revision"],
        extracted_at=extraction.extracted_at if extraction is not None else None,
        issue_count=len(extraction.issues) if extraction is not None else 0,
        task_count=len(extraction.tasks) if extraction is not None else 0,
        decision_count=len(extraction.decisions) if extraction is not None else 0,
        risk_count=len(extraction.risks) if extraction is not None else 0,
        mention_count=len(mentions),
        resolved_entity_count=len(resolved_entity_ids),
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
    ctx: Authorisation = Depends(require_permission(Permission.MEETING_CREATE)),
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
        organisation_id=ctx.organisation_id,
    )
    return MeetingIngestResponse(meeting_id=meeting.meeting_id, status="ingested")


@router.get(
    "",
    response_model=MeetingListResponse,
    summary="List meetings for the current organisation",
    description=(
        "Return tenant-scoped meeting summaries newest first, with durable "
        "processing state and stored extraction counts. The full transcript "
        "is omitted here; GET /meetings/{meeting_id} remains authoritative. "
        "The response is bounded by limit and reports whether more meetings exist."
    ),
)
def list_meetings(
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Maximum meeting summaries to return (1-200).",
    ),
    service: MeetingService = Depends(get_meeting_service),
    ctx: Authorisation = Depends(require_permission(Permission.MEETING_READ)),
) -> MeetingListResponse:
    """Return a bounded, tenant-scoped meeting workspace list."""
    meetings = service.list_meetings(limit=limit + 1)
    has_more = len(meetings) > limit
    selected = meetings[:limit]
    summaries = [_meeting_summary(meeting, ctx) for meeting in selected]
    return MeetingListResponse(
        meetings=summaries,
        limit=limit,
        returned_count=len(summaries),
        has_more=has_more,
    )


@router.get(
    "/{meeting_id}",
    response_model=MeetingResponse,
    summary="Retrieve a meeting by ID",
    description="Fetch the full stored record for a previously ingested meeting.",
)
def get_meeting(
    meeting_id: str,
    service: MeetingService = Depends(get_meeting_service),
    _ctx: Authorisation = Depends(require_permission(Permission.MEETING_READ)),
) -> MeetingResponse:
    """Return a meeting by its ID or raise HTTP 404."""
    meeting = service.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting '{meeting_id}' not found.",
        )
    return _meeting_to_response(meeting)


@router.get(
    "/{meeting_id}/extraction",
    response_model=MeetingExtractionResponse,
    summary="Retrieve the stored extraction for a meeting",
    description=(
        "Return the latest stored extraction without running providers or "
        "mutating source truth. Absence is reported as has_extraction=false, "
        "not as an error."
    ),
)
def get_meeting_extraction(
    meeting_id: str,
    meeting_service: MeetingService = Depends(get_meeting_service),
    extraction_service: ExtractionService = Depends(get_extraction_service),
    _ctx: Authorisation = Depends(require_permission(Permission.MEETING_READ)),
) -> MeetingExtractionResponse:
    """Return stored extraction for a tenant-scoped meeting."""
    if meeting_service.get_meeting(meeting_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting '{meeting_id}' not found.",
        )
    result = extraction_service.get_extraction_result(meeting_id)
    return MeetingExtractionResponse(
        meeting_id=meeting_id,
        has_extraction=result is not None,
        extraction=_extraction_to_response(result) if result is not None else None,
    )


@router.get(
    "/{meeting_id}/processing",
    response_model=MeetingProcessingResponse,
    summary="Retrieve durable processing state for a meeting",
    description=(
        "Return the durable source/extraction/job/semantic consistency state "
        "for the current source revision. No percentages are invented: the "
        "status vocabulary is CURRENT, PENDING, INCOMPLETE, FAILED, or STALE."
    ),
)
def get_meeting_processing(
    meeting_id: str,
    service: MeetingService = Depends(get_meeting_service),
    ctx: Authorisation = Depends(require_permission(Permission.MEETING_READ)),
) -> MeetingProcessingResponse:
    """Return tenant-scoped processing state for one meeting."""
    meeting = service.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting '{meeting_id}' not found.",
        )
    consistency = _meeting_consistency(meeting_id, ctx)
    return _processing_response(meeting_id, meeting.source_revision, consistency)


@router.get(
    "/{meeting_id}/mentions",
    response_model=MeetingMentionsResponse,
    summary="Retrieve current-revision mentions for a meeting",
    description=(
        "Return tenant-scoped entity mentions stamped with the meeting's "
        "current source revision. Older or future revisions are excluded so "
        "past observations never masquerade as current."
    ),
)
def get_meeting_mentions(
    meeting_id: str,
    service: MeetingService = Depends(get_meeting_service),
    ctx: Authorisation = Depends(require_permission(Permission.ENTITY_READ)),
) -> MeetingMentionsResponse:
    """Return tenant-scoped current mentions for one meeting."""
    if service.get_meeting(meeting_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting '{meeting_id}' not found.",
        )
    mentions = ctx.repos.mentions.list_current_by_meeting_id(
        meeting_id, _current_revision_lookup(ctx.repos.meetings)
    )
    mentions.sort(key=lambda mention: (mention.created_at, mention.mention_id))
    schemas = [
        MeetingMentionSchema(
            mention_id=mention.mention_id,
            meeting_id=mention.meeting_id,
            entity_type=mention.entity_type.value,
            text=mention.text,
            source_text=mention.source_text,
            entity_id=mention.entity_id,
            resolution_status=mention.resolution_status.value,
            source_revision=int(mention.source_revision or 1),
        )
        for mention in mentions
    ]
    return MeetingMentionsResponse(
        meeting_id=meeting_id,
        mention_count=len(schemas),
        resolved_mention_count=sum(1 for mention in schemas if mention.entity_id is not None),
        mentions=schemas,
    )


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
    ctx: Authorisation = Depends(require_permission(Permission.MEETING_UPDATE)),
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
            organisation_id=ctx.organisation_id,
        )
        try:
            ctx.repos.save_meeting_and_enqueue(meeting, job)
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
    ctx: Authorisation = Depends(require_permission(Permission.PROCESSING_RUN)),
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
        organisation_id=ctx.organisation_id,
    )
    return _extraction_to_response(result)

