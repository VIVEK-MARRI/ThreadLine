"""Reliable in-process worker over the durable background-job repository."""

from datetime import datetime, timedelta, timezone
import logging
import uuid
from typing import Callable, Optional

from app.models.background_job import BackgroundJob, BackgroundJobStatus, BackgroundJobType
from app.repositories.background_job_repository import (
    AbstractBackgroundJobRepository,
    StaleJobOwnershipError,
)

logger = logging.getLogger(__name__)


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


class TransientJobError(Exception):
    """A bounded retry may succeed later."""


class PermanentJobError(Exception):
    """Retrying will not repair this job payload or schema."""


class BackgroundJobScheduler:
    def __init__(self, repository: AbstractBackgroundJobRepository, max_attempts: int = 3) -> None:
        self._repository = repository
        self._max_attempts = max_attempts

    def enqueue(
        self,
        job_type: BackgroundJobType,
        payload_id: str,
        now: Optional[datetime] = None,
        processing_revision: Optional[str] = None,
    ) -> BackgroundJob:
        job = self.build_job(job_type, payload_id, now, processing_revision)
        result = self._repository.enqueue(job)
        logger.info("job_created job_id=%s job_type=%s payload_id=%s", result.job_id, result.job_type.value, result.payload_id)
        return result

    def build_job(
        self,
        job_type: BackgroundJobType,
        payload_id: str,
        now: Optional[datetime] = None,
        processing_revision: Optional[str] = None,
    ) -> BackgroundJob:
        timestamp = now or datetime.now(timezone.utc)
        return BackgroundJob(
            job_id=(
                f"{job_type.value}:{payload_id}"
                if processing_revision is None
                else f"{job_type.value}:{payload_id}:{processing_revision}"
            ),
            job_type=job_type,
            payload_id=payload_id,
            processing_revision=processing_revision,
            max_attempts=self._max_attempts,
            created_at=timestamp,
        )

    def cancel(self, job_id: str, now: Optional[datetime] = None) -> BackgroundJob:
        # `now` is accepted for backward compatibility but never drives the
        # durable mutation: the repository owns the authoritative clock.
        return self._repository.cancel(job_id)


class BackgroundWorkerService:
    """Poll and execute durable jobs without claiming work after shutdown.

    The worker carries its own `clock` for computing scheduling fields
    (e.g. `next_retry_at`).  Durable lease validation is always performed by
    the repository against *its* clock, so a stale snapshot can never extend
    a lease or checkpoint a job after ownership expired.
    """

    def __init__(
        self,
        repository: AbstractBackgroundJobRepository,
        handlers: dict[BackgroundJobType, Callable[[BackgroundJob], None]],
        worker_id: Optional[str] = None,
        lease_seconds: int = 60,
        backoff_seconds: int = 1,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._repository = repository
        self._handlers = handlers
        self._worker_id = worker_id or str(uuid.uuid4())
        self._lease_seconds = lease_seconds
        self._backoff_seconds = backoff_seconds
        self._clock = clock or _default_clock
        self._stopping = False

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def shutdown(self) -> None:
        """Stop future claims; an already running handler is not cancelled blindly."""
        self._stopping = True

    def recover(self) -> list[BackgroundJob]:
        recovered = self._repository.recover_stale()
        for job in recovered:
            logger.info("job_recovered job_id=%s job_type=%s", job.job_id, job.job_type.value)
        return recovered

    def run_once(self, now: Optional[datetime] = None) -> Optional[BackgroundJob]:
        if self._stopping:
            return None
        cutoff = now or self._clock()
        self.recover()
        candidates = [
            job for job in self._repository.list()
            if job.status in {BackgroundJobStatus.PENDING, BackgroundJobStatus.RETRY_WAITING}
            and (job.next_retry_at is None or job.next_retry_at <= cutoff)
        ]
        if not candidates:
            return None
        claimed = None
        for candidate in candidates:
            claimed = self._repository.claim(candidate.job_id, self._worker_id, self._lease_seconds)
            if claimed is not None:
                break
        if claimed is None:
            return None
        logger.info("job_claimed job_id=%s attempt=%s", claimed.job_id, claimed.attempts)
        handler = self._handlers.get(claimed.job_type)
        try:
            if handler is None:
                return self._fail(claimed, PermanentJobError("unsupported job type"))
            handler(claimed)
            try:
                self._repository.checkpoint(claimed.job_id, "COMPLETED", self._worker_id)
                result = self._repository.transition(
                    claimed.job_id,
                    BackgroundJobStatus.SUCCEEDED,
                    owner_id=self._worker_id,
                    last_error=None,
                    error_type=None,
                )
            except StaleJobOwnershipError:
                logger.warning(
                    "job_lease_lost_on_completion job_id=%s worker_id=%s attempt=%s",
                    claimed.job_id, self._worker_id, claimed.attempts,
                )
                return None
            logger.info("job_completed job_id=%s attempt=%s", result.job_id, result.attempts)
            return result
        except PermanentJobError as exc:
            try:
                return self._fail(claimed, exc, "PERMANENT")
            except StaleJobOwnershipError:
                logger.warning("job_lease_lost job_id=%s worker_id=%s", claimed.job_id, self._worker_id)
                return None
        except StaleJobOwnershipError as exc:
            logger.warning(
                "job_lease_lost job_id=%s worker_id=%s attempt=%s: %s",
                claimed.job_id, self._worker_id, claimed.attempts, exc,
            )
            return None
        except Exception as exc:
            logger.exception(
                "job_processing_error job_id=%s meeting_id=%s revision=%s "
                "stage=%s worker_id=%s attempt=%s",
                claimed.job_id,
                claimed.payload_id,
                claimed.processing_revision,
                claimed.stage,
                self._worker_id,
                claimed.attempts,
            )
            try:
                return self._retry_or_fail(claimed, exc)
            except StaleJobOwnershipError:
                logger.warning("job_lease_lost job_id=%s worker_id=%s", claimed.job_id, self._worker_id)
                return None

    def _retry_or_fail(self, job: BackgroundJob, exc: Exception) -> BackgroundJob:
        if job.attempts >= job.max_attempts:
            return self._fail(job, exc, "TRANSIENT")
        delay = self._backoff_seconds * (2 ** max(0, job.attempts - 1))
        now = self._clock()
        result = self._repository.transition(
            job.job_id,
            BackgroundJobStatus.RETRY_WAITING,
            owner_id=self._worker_id,
            next_retry_at=now + timedelta(seconds=delay),
            last_error=str(exc),
            error_type="TRANSIENT",
            worker_id=None,
        )
        logger.warning("job_retry job_id=%s attempt=%s next_retry_at=%s", result.job_id, result.attempts, result.next_retry_at)
        return result

    def _fail(self, job: BackgroundJob, exc: Exception, error_type: str = "PERMANENT") -> BackgroundJob:
        result = self._repository.transition(
            job.job_id,
            BackgroundJobStatus.FAILED,
            owner_id=self._worker_id,
            last_error=str(exc),
            error_type=error_type,
        )
        logger.error("job_failed job_id=%s attempt=%s stage=%s", result.job_id, result.attempts, result.stage)
        return result

    def checkpoint(self, job_id: str, stage: str) -> BackgroundJob:
        logger.info("stage_completed job_id=%s stage=%s", job_id, stage)
        current = self._repository.get(job_id)
        if current is not None and current.status == BackgroundJobStatus.SUCCEEDED and current.stage == stage:
            return current
        return self._repository.checkpoint(job_id, stage, self._worker_id)
