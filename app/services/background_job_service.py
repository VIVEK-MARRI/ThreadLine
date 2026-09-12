"""Small bounded in-process background processing abstraction."""

from datetime import datetime, timezone
import logging
from typing import Callable

from app.models.background_job import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
)

logger = logging.getLogger(__name__)


class BackgroundJobService:
    """Run bounded, idempotent handlers without introducing a queue service."""

    def __init__(self, handlers: dict[BackgroundJobType, Callable[[str], None]], max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._handlers = handlers
        self._max_attempts = max_attempts
        self._jobs: dict[str, BackgroundJob] = {}

    def create_job(self, job_type: BackgroundJobType, payload_id: str) -> BackgroundJob:
        job_id = f"{job_type.value}:{payload_id}"
        existing = self._jobs.get(job_id)
        if existing is not None:
            return existing
        job = BackgroundJob(
            job_id=job_id,
            job_type=job_type,
            created_at=datetime.now(timezone.utc),
        )
        self._jobs[job_id] = job
        logger.info("background job created type=%s job_id=%s", job_type.value, job_id)
        return job

    def run(self, job_id: str) -> BackgroundJob:
        job = self._jobs[job_id]
        if job.status == BackgroundJobStatus.COMPLETED:
            return job
        handler = self._handlers[job.job_type]
        job.status = BackgroundJobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        while job.attempts < self._max_attempts:
            job.attempts += 1
            try:
                logger.info("background job started type=%s job_id=%s attempt=%s", job.job_type.value, job.job_id, job.attempts)
                handler(job_id.split(":", 1)[1])
            except Exception as exc:
                job.last_error = str(exc)
                logger.warning("background job retry type=%s job_id=%s attempt=%s", job.job_type.value, job.job_id, job.attempts)
                if job.attempts >= self._max_attempts:
                    job.status = BackgroundJobStatus.FAILED
                    job.completed_at = datetime.now(timezone.utc)
                    logger.error("background job failed type=%s job_id=%s", job.job_type.value, job.job_id)
                    return job
                continue
            job.status = BackgroundJobStatus.COMPLETED
            job.last_error = None
            job.completed_at = datetime.now(timezone.utc)
            logger.info("background job completed type=%s job_id=%s", job.job_type.value, job.job_id)
            return job
        return job

    def get(self, job_id: str) -> BackgroundJob | None:
        return self._jobs.get(job_id)
