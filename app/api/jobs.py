"""Read-only internal diagnostics for durable background jobs."""

from fastapi import APIRouter

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
def job_health() -> dict:
    """Return safe queue counts and stale-running count."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    running = _job_repository.list()
    stale = sum(
        1 for job in running
        if job.status.value == "RUNNING" and job.lease_until and job.lease_until <= now
    )
    return {
        "worker_enabled": settings.background_worker_enabled,
        "counts": _job_repository.counts(),
        "stale_running": stale,
        "oldest_pending_age_seconds": _job_repository.oldest_pending_age_seconds(now),
    }