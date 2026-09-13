"""Threadline FastAPI application entry point.

Wires together configuration, routers, and middleware.
Keep this file thin — it delegates everything to the api layer.
"""

from datetime import datetime, timezone
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.api.meetings import router as meetings_router
from app.api.entities import router as entities_router
from app.api.attention import router as attention_router
from app.api.portfolio import router as portfolio_router
from app.api.changes import router as changes_router
from app.api.query import router as query_router
from app.api.jobs import router as jobs_router
from app.schemas.meeting import HealthResponse
from app.api.meetings import get_source_store, get_meeting_repository
from app.api.query import _semantic_repository
from app.api.jobs import get_job_repository
from app.models.background_job import BackgroundJobType
from app.services.background_worker_service import BackgroundWorkerService
from app.services.meeting_processing_service import MeetingProcessingService, ProcessingStage
from app.api.meetings import get_extraction_service
from app.api.meetings import _extraction_repository
from app.api.entities import (
    _dependency_repository,
    _entity_repository,
    _mention_repository,
    get_action_recommendation_service,
    get_attention_service,
    get_correlation_service,
    get_dependency_graph_service,
    get_entity_relationship_service,
    get_impact_analysis_service,
    get_insight_service,
    get_organisational_memory_service,
    get_portfolio_intelligence_service,
    get_resolution_service,
    get_temporal_state_service,
    get_unified_timeline_service,
)
from app.api.changes import get_organisation_change_intelligence_service
from app.api.query import _semantic_indexing_service, get_evidence_retrieval_service
from app.services.dependency_resolution_service import DependencyResolutionService
from app.services.meeting_pipeline_orchestrator import MeetingPipelineOrchestrator
from app.services.entity_observation_service import EntityObservationService
from app.repositories.background_job_repository import StaleJobOwnershipError

_application_worker: BackgroundWorkerService | None = None
_worker_thread: threading.Thread | None = None
_worker_stop = threading.Event()


def _build_application_worker() -> BackgroundWorkerService:
    repository = get_job_repository()

    correlation = get_correlation_service()
    temporal = get_temporal_state_service()
    memory = get_organisational_memory_service()
    insights = get_insight_service()
    attention = get_attention_service()
    actions = get_action_recommendation_service()
    timeline = get_unified_timeline_service()
    relationships = get_entity_relationship_service()
    dependency_graph = get_dependency_graph_service()
    impact = get_impact_analysis_service()
    changes = get_organisation_change_intelligence_service()
    portfolio = get_portfolio_intelligence_service()
    evidence = get_evidence_retrieval_service(
        timeline_svc=timeline, memory_svc=memory, insight_svc=insights,
        attention_svc=attention, action_svc=actions,
        dependency_graph_svc=dependency_graph, impact_svc=impact,
        org_change_svc=changes, portfolio_svc=portfolio,
        relationship_svc=relationships,
    )
    dependency_resolution = DependencyResolutionService(
        entity_repo=_entity_repository,
        mention_repo=_mention_repository,
        dependency_repo=_dependency_repository,
    )
    pipeline = MeetingPipelineOrchestrator(
        extraction_repository=_extraction_repository,
        mention_repository=_mention_repository,
        resolution_service=get_resolution_service(),
        dependency_resolution_service=dependency_resolution,
        evidence_retrieval_service=evidence,
        semantic_indexing_service=_semantic_indexing_service,
        derived_services=(
            lambda entity_id, now: correlation.get_entity_correlations(entity_id),
            lambda entity_id, now: temporal.get_entity_timeline(entity_id),
            lambda entity_id, now: memory.get_entity_memory(entity_id),
            lambda entity_id, now: insights.get_entity_insights(entity_id, now),
            lambda entity_id, now: attention.get_entity_attention(entity_id, now),
            lambda entity_id, now: actions.get_entity_actions(entity_id, now),
            lambda entity_id, now: timeline.get_unified_timeline(entity_id),
            lambda entity_id, now: relationships.get_relationship_graph(entity_id),
            lambda entity_id, now: dependency_graph.build_dependency_graph(entity_id),
            lambda entity_id, now: impact.get_entity_impacts(entity_id, now),
            lambda entity_id, now: changes.get_changes(current_time=now, entity_id=entity_id),
        ),
        organisation_derived_services=(lambda now: portfolio.get_portfolio(now),),
        observation_service=EntityObservationService(
            get_meeting_repository(), _entity_repository, _mention_repository
        ),
        meeting_repository=get_meeting_repository(),
    )

    def process_meeting(job):
        def assert_owned():
            current = repository.get(job.job_id)
            now = datetime.now(timezone.utc)
            if (
                current is None
                or current.status.value != "RUNNING"
                or current.worker_id != job.worker_id
                or current.lease_until is None
                or current.lease_until <= now
                or current.processing_revision != job.processing_revision
            ):
                raise StaleJobOwnershipError(
                    f"worker {job.worker_id} no longer owns processing revision "
                    f"{job.processing_revision} for {job.job_id}"
                )

        pipeline.set_ownership_checker(assert_owned)
        extraction_service = get_extraction_service()
        if hasattr(extraction_service, "set_ownership_checker"):
            extraction_service.set_ownership_checker(assert_owned)
        handlers = {
            ProcessingStage.EXTRACTED: lambda meeting_id: extraction_service.extract_meeting(meeting_id),
            ProcessingStage.RESOLVED: pipeline.resolve_meeting,
            ProcessingStage.RELATIONSHIPS_PERSISTED: pipeline.persist_relationships,
            ProcessingStage.DERIVED_INTELLIGENCE: pipeline.derive_intelligence,
            ProcessingStage.SEMANTIC_INDEXED: pipeline.index_semantic_evidence,
        }
        MeetingProcessingService(repository, handlers).process(job)

    return BackgroundWorkerService(
        repository,
        {BackgroundJobType.MEETING_PROCESSING: process_meeting},
        lease_seconds=settings.background_lease_seconds,
    )


def _worker_loop(worker: BackgroundWorkerService) -> None:
    while not _worker_stop.is_set():
        worker.run_once()
        _worker_stop.wait(settings.background_poll_interval_seconds)

def start_background_worker() -> None:
    global _application_worker, _worker_thread
    if not settings.background_worker_enabled or _worker_thread is not None:
        return
    _worker_stop.clear()
    _application_worker = _build_application_worker()
    _worker_thread = threading.Thread(
        target=_worker_loop, args=(_application_worker,), daemon=True, name="threadline-worker"
    )
    _worker_thread.start()


def stop_background_worker() -> None:
    global _application_worker, _worker_thread
    if _application_worker is None:
        return
    _application_worker.shutdown()
    _worker_stop.set()
    if _worker_thread is not None:
        _worker_thread.join(timeout=max(1.0, settings.background_poll_interval_seconds + 1.0))
    _application_worker = None
    _worker_thread = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_background_worker()
    try:
        yield
    finally:
        stop_background_worker()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    description=(
        "Threadline transforms meeting data into structured organisational memory, "
        "insights, and proactive risk intelligence."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(meetings_router, prefix=settings.api_v1_prefix)
app.include_router(entities_router, prefix=settings.api_v1_prefix)
app.include_router(attention_router, prefix=settings.api_v1_prefix)
app.include_router(portfolio_router, prefix=settings.api_v1_prefix)
app.include_router(changes_router, prefix=settings.api_v1_prefix)
app.include_router(query_router, prefix=settings.api_v1_prefix)
app.include_router(jobs_router, prefix=settings.api_v1_prefix)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["Health"],
    summary="Health check",
    description="Returns the operational status of the Threadline API.",
)
def health() -> HealthResponse:
    """Lightweight liveness probe."""
    return HealthResponse(status="healthy")


@app.get(
    "/health/diagnostics",
    tags=["Health"],
    summary="Internal storage diagnostics",
)
def health_diagnostics() -> dict:
    """Return backend availability without exposing storage internals."""
    source_store = get_source_store()
    from app.api.jobs import get_job_repository

    job_repository = get_job_repository()
    semantic_available = True
    try:
        _semantic_repository.count()
    except Exception:
        semantic_available = False
    return {
        "source_backend": settings.source_repository_backend,
        "source_database": source_store.health() if source_store else True,
        "semantic_index": semantic_available,
        "background_jobs": True,
        "background_job_counts": job_repository.counts(),
        "stale_running_jobs": sum(
            1 for job in job_repository.list()
            if job.status.value == "RUNNING" and job.lease_until and job.lease_until <= datetime.now(timezone.utc)
        ),
    }
