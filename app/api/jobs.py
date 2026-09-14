"""Read-only internal diagnostics for durable background jobs."""

from fastapi import APIRouter, Depends

from app.api.auth import Authorisation, get_request_context
from app.auth.models import Permission
from app.api.meetings import get_source_store
from app.core.config import settings
from app.repositories.background_job_repository import (
    AbstractBackgroundJobRepository,
    InMemoryBackgroundJobRepository,
    SQLiteBackgroundJobRepository,
)
from app.services.background_worker_service import BackgroundJobScheduler
from app.models.background_job import BackgroundJobType

router = APIRouter(prefix="/health", tags=["Health"])

_source_store = get_source_store()
if settings.source_repository_backend.lower() == "database":
    if _source_store is None:
        raise RuntimeError("Database source backend was not initialized")
    _job_repository: AbstractBackgroundJobRepository = SQLiteBackgroundJobRepository(_source_store)
else:
    _job_repository = InMemoryBackgroundJobRepository()


def get_job_repository() -> AbstractBackgroundJobRepository:
    return _job_repository


_scheduler = BackgroundJobScheduler(
    _job_repository,
    max_attempts=settings.background_max_attempts,
)


def get_job_scheduler() -> BackgroundJobScheduler:
    return _scheduler


@router.get("/jobs")
def job_health(ctx: Authorisation = Depends(get_request_context)) -> dict:
    """Return safe queue counts and stale-running count.

    Open while bootstrap is open (no users yet — the E2E worker polls this).
    Once authentication is active, any member may view their OWN
    organisation's counts (JOB_READ); counts are tenant-scoped so queue
    depth never leaks across tenants.  Full backend diagnostics stay on
    /health/diagnostics (DIAGNOSTICS_READ, admin+).
    """
    from datetime import datetime, timezone

    if not ctx.anonymous:
        from app.auth.models import has_permission as _has_permission

        if not _has_permission(ctx.role, Permission.JOB_READ):
            from fastapi import HTTPException, status as _status

            raise HTTPException(
                status_code=_status.HTTP_403_FORBIDDEN,
                detail=f"role '{ctx.role.value if ctx.role else None}' lacks permission "
                f"'{Permission.JOB_READ.value}'",
            )
        repository = ctx.repos.jobs
    else:
        repository = _job_repository
    now = datetime.now(timezone.utc)
    running = repository.list()
    stale = sum(
        1 for job in running
        if job.status.value == "RUNNING" and job.lease_until and job.lease_until <= now
    )
    return {
        "worker_enabled": settings.background_worker_enabled,
        "counts": repository.counts(),
        "stale_running": stale,
        "oldest_pending_age_seconds": repository.oldest_pending_age_seconds(now),
    }