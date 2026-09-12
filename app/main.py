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
from app.api.meetings import get_source_store
from app.api.query import _semantic_repository
from app.api.jobs import get_job_repository
from app.models.background_job import BackgroundJobType
from app.services.background_worker_service import BackgroundWorkerService
from app.services.meeting_processing_service import MeetingProcessingService, ProcessingStage
from app.api.meetings import get_extraction_service

_application_worker: BackgroundWorkerService | None = None
_worker_thread: threading.Thread | None = None
_worker_stop = threading.Event()


def _build_application_worker() -> BackgroundWorkerService:
    repository = get_job_repository()

    def process_meeting(job):
        handlers = {
            ProcessingStage.EXTRACTED: lambda meeting_id: get_extraction_service().extract_meeting(meeting_id),
            # These handlers are explicit lifecycle boundaries. Domain-specific
            # resolution/derived services remain independently retryable.
            ProcessingStage.RESOLVED: lambda meeting_id: None,
            ProcessingStage.RELATIONSHIPS_PERSISTED: lambda meeting_id: None,
            ProcessingStage.DERIVED_INTELLIGENCE: lambda meeting_id: None,
            ProcessingStage.SEMANTIC_INDEXED: lambda meeting_id: None,
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
