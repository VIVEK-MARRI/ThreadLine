"""Threadline FastAPI application entry point.

Wires together configuration, routers, and middleware.
Keep this file thin — it delegates everything to the api layer.
"""

from datetime import datetime, timezone

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

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Threadline transforms meeting data into structured organisational memory, "
        "insights, and proactive risk intelligence."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
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
