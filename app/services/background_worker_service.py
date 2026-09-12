"""Reliable in-process worker over the durable background-job repository."""

from datetime import datetime, timedelta, timezone
import logging
import uuid
from typing import Callable, Optional

from app.models.background_job import BackgroundJob, BackgroundJobStatus, BackgroundJobType
from app.repositories.background_job_repository import AbstractBackgroundJobRepository

logger = logging.getLogger(__name__)


class TransientJobError(Exception):
    """A bounded retry may succeed later."""


class PermanentJobError(Exception):
    """Retrying will not repair this job payload or schema."""


class BackgroundJobScheduler:
    def __init__(self, repository: AbstractBackgroundJobRepository, max_attempts: int = 3) -> None:
        self._repository = repository
        self._max_attempts = max_attempts

    def enqueue(self, job_type: BackgroundJobType, payload_id: str, now: Optional[datetime] = None) -> BackgroundJob:
        timestamp = now or datetime.now(timezone.utc)
        job = BackgroundJob(
            job_id=f"{job_type.value}:{payload_id}",
            job_type=job_type,
            payload_id=payload_id,
            max_attempts=self._max_attempts,
            created_at=timestamp,
        )
        result = self._repository.enqueue(job)
        logger.info("job_created job_id=%s job_type=%s payload_id=%s", result.job_id, result.job_type.value, result.payload_id)
        return result

    def cancel(self, job_id: str, now: Optional[datetime] = None) -> BackgroundJob:
        return self._repository.cancel(job_id, now or datetime.now(timezone.utc))


class BackgroundWorkerService:
    """Poll and execute durable jobs without claiming work after shutdown."""

    def __init__(
        self,
        repository: AbstractBackgroundJobRepository,
        handlers: dict[BackgroundJobType, Callable[[BackgroundJob], None]],
        worker_id: Optional[str] = None,
        lease_seconds: int = 60,
        backoff_seconds: int = 1,
    ) -> None:
        self._repository = repository
        self._handlers = handlers
        self._worker_id = worker_id or str(uuid.uuid4())
        self._lease_seconds = lease_seconds
        self._backoff_seconds = backoff_seconds
        self._stopping = False

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def shutdown(self) -> None:
        """Stop future claims; an already running handler is not cancelled blindly."""
        self._stopping = True

    def recover(self, now: Optional[datetime] = None) -> list[BackgroundJob]:
        recovered = self._repository.recover_stale(now or datetime.now(timezone.utc))
        for job in recovered:
            logger.info("job_recovered job_id=%s job_type=%s", job.job_id, job.job_type.value)
        return recovered

    def run_once(self, now: Optional[datetime] = None) -> Optional[BackgroundJob]:
        if self._stopping:
            return None
        current = now or datetime.now(timezone.utc)
        self.recover(current)
        candidates = self._repository.list()
        candidates = [
            job for job in candidates
            if job.status in {BackgroundJobStatus.PENDING, BackgroundJobStatus.RETRY_WAITING}
            and (job.next_retry_at is None or job.next_retry_at <= current)
        ]
        if not candidates:
            return None
        claimed = self._repository.claim(candidates[0].job_id, self._worker_id, current, self._lease_seconds)
        if claimed is None:
            return None
        logger.info("job_claimed job_id=%s attempt=%s", claimed.job_id, claimed.attempts)
        handler = self._handlers.get(claimed.job_type)
        if handler is None:
            return self._fail(claimed, current, PermanentJobError("unsupported job type"))
        try:
            handler(claimed)
        except PermanentJobError as exc:
            return self._fail(claimed, current, exc, "PERMANENT")
        except Exception as exc:
            return self._retry_or_fail(claimed, current, exc)
        result = self._repository.transition(
            claimed.job_id,
            BackgroundJobStatus.SUCCEEDED,
            current,
            last_error=None,
            error_type=None,
        )
        logger.info("job_completed job_id=%s attempt=%s", result.job_id, result.attempts)
        return result

    def _retry_or_fail(self, job: BackgroundJob, now: datetime, exc: Exception) -> BackgroundJob:
        if job.attempts >= job.max_attempts:
            return self._fail(job, now, exc, "TRANSIENT")
        delay = self._backoff_seconds * (2 ** max(0, job.attempts - 1))
        result = self._repository.transition(
            job.job_id,
            BackgroundJobStatus.RETRY_WAITING,
            now,
            next_retry_at=now + timedelta(seconds=delay),
            last_error=str(exc),
            error_type="TRANSIENT",
            worker_id=None,
        )
        logger.warning("job_retry job_id=%s attempt=%s next_retry_at=%s", result.job_id, result.attempts, result.next_retry_at)
        return result

    def _fail(self, job: BackgroundJob, now: datetime, exc: Exception, error_type: str = "PERMANENT") -> BackgroundJob:
        result = self._repository.transition(
            job.job_id,
            BackgroundJobStatus.FAILED,
            now,
            last_error=str(exc),
            error_type=error_type,
        )
        logger.error("job_failed job_id=%s attempt=%s stage=%s", result.job_id, result.attempts, result.stage)
        return result

    def checkpoint(self, job_id: str, stage: str) -> BackgroundJob:
        logger.info("stage_completed job_id=%s stage=%s", job_id, stage)
        return self._repository.checkpoint(job_id, stage)
