"""Threadline FastAPI application entry point.

Wires together configuration, routers, and middleware.
Keep this file thin — it delegates everything to the api layer.
"""

from datetime import datetime, timezone
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.core.config import settings
from app.api.auth import router as auth_router
from app.api.organisations import router as organisations_router
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
from app.api.query import _semantic_repository
from app.api.jobs import get_job_repository
from app.models.background_job import BackgroundJobType
from app.services.background_worker_service import BackgroundWorkerService
from app.services.meeting_processing_service import MeetingProcessingService, ProcessingStage
from app.api.meetings import get_source_store, get_meeting_repository
from app.repositories.background_job_repository import StaleJobOwnershipError

_application_worker: BackgroundWorkerService | None = None
_worker_thread: threading.Thread | None = None
_worker_stop = threading.Event()


def _build_tenant_graph(organisation_id: str) -> dict:
    """Build the full processing service graph scoped to one organisation.

    The worker derives its ENTIRE tenant context from the durable job's
    organisation_id (never from request state — there is no request).  Every
    repository below is a tenant-scoped view, so a job for Tenant A cannot
    read, resolve, traverse, index, or query Tenant B data at any stage.
    """
    from app.api.auth import _tenant_repos
    from app.api.meetings import _extraction_provider
    from app.api.query import _embedding_provider
    from app.entity_resolution.lexical_candidate_generator import LexicalCandidateGenerator
    from app.entity_resolution.lexical_candidate_scorer import LexicalCandidateScorer
    from app.entity_resolution.resolution_policy import ThresholdResolutionPolicy
    from app.services.action_recommendation_service import ActionRecommendationService
    from app.services.attention_service import AttentionService
    from app.services.candidate_scoring_service import CandidateScoringService
    from app.services.correlation_service import CorrelationService
    from app.services.dependency_graph_service import DependencyGraphService
    from app.services.dependency_resolution_service import DependencyResolutionService
    from app.services.entity_observation_service import EntityObservationService
    from app.services.entity_relationship_service import EntityRelationshipService
    from app.services.evidence_retrieval_service import EvidenceRetrievalService
    from app.services.extraction_service import ExtractionService
    from app.services.impact_analysis_service import ImpactAnalysisService
    from app.services.insight_service import InsightService
    from app.services.meeting_pipeline_orchestrator import MeetingPipelineOrchestrator
    from app.services.organisational_memory_service import OrganisationalMemoryService
    from app.services.organisation_change_intelligence_service import (
        OrganisationChangeIntelligenceService,
    )
    from app.services.portfolio_intelligence_service import PortfolioIntelligenceService
    from app.services.resolution_service import ResolutionService
    from app.services.semantic_indexing_service import SemanticIndexingService
    from app.services.temporal_state_service import TemporalStateService
    from app.services.unified_timeline_service import UnifiedTimelineService
    from app.temporal.state_interpreter import KeywordStateInterpreter
    from app.temporal.transition_policy import DefaultTransitionPolicy

    repos = _tenant_repos(organisation_id)

    def _revision_lookup(meeting_id):
        if meeting_id is None:
            return None
        meeting = repos.meetings.get_by_id(meeting_id)
        if meeting is None:
            return None
        try:
            return int(getattr(meeting, "source_revision", 1) or 1)
        except (TypeError, ValueError):
            return None

    interpreter, policy = KeywordStateInterpreter(), DefaultTransitionPolicy()
    scoring = CandidateScoringService(
        mention_repo=repos.mentions,
        entity_repo=repos.entities,
        generator=LexicalCandidateGenerator(),
        scorer=LexicalCandidateScorer(),
    )
    resolution = ResolutionService(
        mention_repo=repos.mentions,
        entity_repo=repos.entities,
        scoring_service=scoring,
        policy=ThresholdResolutionPolicy(),
    )
    correlation = CorrelationService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        meeting_repo=repos.meetings,
    )
    temporal = TemporalStateService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        meeting_repo=repos.meetings,
        interpreter=interpreter,
        policy=policy,
    )
    memory = OrganisationalMemoryService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        meeting_repo=repos.meetings,
        interpreter=interpreter,
        policy=policy,
    )
    insights = InsightService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        meeting_repo=repos.meetings,
        interpreter=interpreter,
        policy=policy,
    )
    attention = AttentionService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        meeting_repo=repos.meetings,
        interpreter=interpreter,
        policy=policy,
    )
    actions = ActionRecommendationService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        meeting_repo=repos.meetings,
        interpreter=interpreter,
        policy=policy,
    )
    timeline = UnifiedTimelineService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        meeting_repo=repos.meetings,
        interpreter=interpreter,
        policy=policy,
    )
    relationships = EntityRelationshipService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        dependency_repo=repos.dependencies,
        current_revision_lookup=_revision_lookup,
    )
    dependency_graph = DependencyGraphService(
        dependency_repo=repos.dependencies,
        entity_repo=repos.entities,
        current_revision_lookup=_revision_lookup,
    )
    impact = ImpactAnalysisService(
        entity_repo=repos.entities,
        relationship_service=relationships,
        temporal_service=temporal,
        insight_service=insights,
        attention_service=attention,
        dependency_graph_service=dependency_graph,
    )
    changes = OrganisationChangeIntelligenceService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        meeting_repo=repos.meetings,
        interpreter=interpreter,
        policy=policy,
        dependency_repo=repos.dependencies,
        current_revision_lookup=_revision_lookup,
    )
    portfolio = PortfolioIntelligenceService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        meeting_repo=repos.meetings,
        interpreter=interpreter,
        policy=policy,
        dependency_repo=repos.dependencies,
    )
    evidence = EvidenceRetrievalService(
        entity_repo=repos.entities,
        meeting_repo=repos.meetings,
        timeline_svc=timeline,
        memory_svc=memory,
        insight_svc=insights,
        attention_svc=attention,
        action_svc=actions,
        dependency_graph_svc=dependency_graph,
        impact_svc=impact,
        org_change_svc=changes,
        portfolio_svc=portfolio,
        relationship_svc=relationships,
    )
    dependency_resolution = DependencyResolutionService(
        entity_repo=repos.entities,
        mention_repo=repos.mentions,
        dependency_repo=repos.dependencies,
    )
    semantic_indexing = SemanticIndexingService(
        embedding_provider=_embedding_provider,
        repository=repos.semantic,
        embedding_model_name=settings.active_embedding_model,
        representation_version=settings.active_representation_version,
        current_revision_lookup=_revision_lookup,
    )
    pipeline = MeetingPipelineOrchestrator(
        extraction_repository=repos.extractions,
        mention_repository=repos.mentions,
        resolution_service=resolution,
        dependency_resolution_service=dependency_resolution,
        evidence_retrieval_service=evidence,
        semantic_indexing_service=semantic_indexing,
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
            repos.meetings, repos.entities, repos.mentions
        ),
        meeting_repository=repos.meetings,
    )
    extraction_service = ExtractionService(
        meeting_repository=repos.meetings,
        extraction_repository=repos.extractions,
        provider=_extraction_provider,
    )
    return {
        "repos": repos,
        "pipeline": pipeline,
        "extraction_service": extraction_service,
    }


def _build_application_worker() -> BackgroundWorkerService:
    from app.auth.constants import DEFAULT_ORGANISATION_ID

    # The job repository stays UNSCOPED: the worker must claim every
    # organisation's jobs.  Tenant scope is derived per job from the durable
    # job row and enforced by the scoped service graph below.
    repository = get_job_repository()

    def process_meeting(job):
        from app.services.background_worker_service import PermanentJobError

        organisation_id = getattr(job, "organisation_id", None) or DEFAULT_ORGANISATION_ID
        graph = _build_tenant_graph(organisation_id)
        scoped_meetings = graph["repos"].meetings
        pipeline = graph["pipeline"]
        extraction_service = graph["extraction_service"]

        # Cross-tenant job barrier: the meeting this job targets must belong
        # to the job's organisation.  A missing meeting keeps the historical
        # retry path (MeetingNotFound flow downstream); a meeting owned by a
        # DIFFERENT organisation is a security violation — fail permanently,
        # never process, never retry.
        if scoped_meetings.get_by_id(job.payload_id) is None:
            if get_meeting_repository().get_by_id(job.payload_id) is not None:
                raise PermanentJobError(
                    f"job '{job.job_id}' targets a meeting outside organisation "
                    f"'{organisation_id}'; refusing to process"
                )

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

app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(organisations_router, prefix=settings.api_v1_prefix)
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
def health_diagnostics(request: Request) -> dict:
    """Return backend availability without exposing storage internals.

    Open while bootstrap is open (no users yet — workers poll this).  Once
    authentication is active, ADMIN/OWNER only and scoped to the caller's
    organisation.
    """
    from fastapi import Depends, HTTPException, status as _status

    from app.api.auth import get_request_context
    from app.api.jobs import get_job_repository
    from app.auth.models import Permission, has_permission

    ctx = get_request_context(request)
    source_store = get_source_store()
    if ctx.anonymous:
        job_repository = get_job_repository()
    else:
        if not has_permission(ctx.role, Permission.DIAGNOSTICS_READ):
            raise HTTPException(
                status_code=_status.HTTP_403_FORBIDDEN,
                detail=f"role '{ctx.role.value if ctx.role else None}' lacks permission "
                f"'{Permission.DIAGNOSTICS_READ.value}'",
            )
        job_repository = ctx.repos.jobs
    semantic_available = True
    try:
        if ctx.anonymous:
            _semantic_repository.count()
        else:
            ctx.repos.semantic.count()
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
