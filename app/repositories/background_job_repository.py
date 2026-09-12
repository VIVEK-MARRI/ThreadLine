"""Durable and in-memory repositories for background job state."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.background_job import BackgroundJob, BackgroundJobStatus, BackgroundJobType
from app.persistence.sqlite_store import SQLiteSourceStore


_ALLOWED_TRANSITIONS = {
    BackgroundJobStatus.PENDING: {BackgroundJobStatus.RUNNING, BackgroundJobStatus.CANCELLED},
    BackgroundJobStatus.RUNNING: {
        BackgroundJobStatus.SUCCEEDED,
        BackgroundJobStatus.RETRY_WAITING,
        BackgroundJobStatus.FAILED,
    },
    BackgroundJobStatus.RETRY_WAITING: {BackgroundJobStatus.RUNNING, BackgroundJobStatus.CANCELLED},
    BackgroundJobStatus.SUCCEEDED: set(),
    BackgroundJobStatus.FAILED: set(),
    BackgroundJobStatus.CANCELLED: set(),
}


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class InvalidJobTransition(ValueError):
    """Raised when a job state transition is not allowed."""


class AbstractBackgroundJobRepository(ABC):
    @abstractmethod
    def enqueue(self, job: BackgroundJob) -> BackgroundJob:
        """Create a job once, returning the existing logical job if present."""
        ...

    @abstractmethod
    def get(self, job_id: str) -> Optional[BackgroundJob]:
        ...

    @abstractmethod
    def claim(self, job_id: str, worker_id: str, now: datetime, lease_seconds: int) -> Optional[BackgroundJob]:
        """Atomically transition an eligible job to RUNNING."""
        ...

    @abstractmethod
    def transition(self, job_id: str, status: BackgroundJobStatus, now: datetime, **fields) -> BackgroundJob:
        ...

    @abstractmethod
    def checkpoint(self, job_id: str, stage: str) -> BackgroundJob:
        ...

    @abstractmethod
    def recover_stale(self, now: datetime) -> list[BackgroundJob]:
        ...

    @abstractmethod
    def cancel(self, job_id: str, now: datetime) -> BackgroundJob:
        ...

    @abstractmethod
    def list(self, status: Optional[BackgroundJobStatus] = None) -> list[BackgroundJob]:
        ...

    def counts(self) -> dict[str, int]:
        return {
            status.value: len(self.list(status))
            for status in BackgroundJobStatus
            if status.name != "COMPLETED"
        }

    def oldest_pending_age_seconds(self, now: datetime) -> Optional[float]:
        jobs = self.list(BackgroundJobStatus.PENDING)
        if not jobs:
            return None
        return max(0.0, (now - min(job.created_at for job in jobs)).total_seconds())


class InMemoryBackgroundJobRepository(AbstractBackgroundJobRepository):
    def __init__(self) -> None:
        from threading import RLock
        self._jobs: dict[str, BackgroundJob] = {}
        self._lock = RLock()

    def enqueue(self, job: BackgroundJob) -> BackgroundJob:
        with self._lock:
            return self._jobs.setdefault(job.job_id, job)

    def get(self, job_id: str) -> Optional[BackgroundJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def claim(self, job_id: str, worker_id: str, now: datetime, lease_seconds: int) -> Optional[BackgroundJob]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in {BackgroundJobStatus.PENDING, BackgroundJobStatus.RETRY_WAITING}:
                return None
            if job.next_retry_at and job.next_retry_at > now:
                return None
            job.status = BackgroundJobStatus.RUNNING
            job.started_at = now
            job.worker_id = worker_id
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.attempts += 1
            return job

    def transition(self, job_id: str, status: BackgroundJobStatus, now: datetime, **fields) -> BackgroundJob:
        with self._lock:
            job = self._jobs[job_id]
            if status not in _ALLOWED_TRANSITIONS[job.status]:
                raise InvalidJobTransition(f"{job.status.value} -> {status.value} is not allowed")
            job.status = status
            for key, value in fields.items():
                setattr(job, key, value)
            if status in {BackgroundJobStatus.SUCCEEDED, BackgroundJobStatus.FAILED, BackgroundJobStatus.CANCELLED}:
                job.completed_at = now
                job.lease_until = None
            return job

    def checkpoint(self, job_id: str, stage: str) -> BackgroundJob:
        with self._lock:
            job = self._jobs[job_id]
            job.stage = stage
            return job

    def recover_stale(self, now: datetime) -> list[BackgroundJob]:
        recovered = []
        with self._lock:
            for job in self._jobs.values():
                if job.status == BackgroundJobStatus.RUNNING and job.lease_until and job.lease_until <= now:
                    if job.attempts < job.max_attempts:
                        job.status = BackgroundJobStatus.RETRY_WAITING
                        job.next_retry_at = now
                        job.worker_id = None
                        job.lease_until = None
                    else:
                        job.status = BackgroundJobStatus.FAILED
                        job.completed_at = now
                    recovered.append(job)
        return recovered

    def cancel(self, job_id: str, now: datetime) -> BackgroundJob:
        return self.transition(job_id, BackgroundJobStatus.CANCELLED, now)

    def list(self, status: Optional[BackgroundJobStatus] = None) -> list[BackgroundJob]:
        with self._lock:
            jobs = list(self._jobs.values())
            if status is not None:
                jobs = [job for job in jobs if job.status == status]
            return sorted(jobs, key=lambda job: job.job_id)


class SQLiteBackgroundJobRepository(AbstractBackgroundJobRepository):
    def __init__(self, store: SQLiteSourceStore) -> None:
        self._store = store

    @staticmethod
    def _from_row(row) -> BackgroundJob:
        return BackgroundJob(
            job_id=row["job_id"], job_type=row["job_type"], payload_id=row["payload_id"],
            status=row["status"], attempts=row["attempts"], max_attempts=row["max_attempts"],
            created_at=_parse(row["created_at"]), started_at=_parse(row["started_at"]),
            completed_at=_parse(row["completed_at"]), last_error=row["last_error"],
            error_type=row["error_type"], next_retry_at=_parse(row["next_retry_at"]),
            lease_until=_parse(row["lease_until"]), worker_id=row["worker_id"], stage=row["stage"],
        )

    def enqueue(self, job: BackgroundJob) -> BackgroundJob:
        with self._store.transaction() as connection:
            connection.execute(
                """INSERT INTO background_jobs
                (job_id, job_type, payload_id, status, attempts, max_attempts, created_at,
                 started_at, completed_at, last_error, error_type, next_retry_at, lease_until, worker_id, stage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO NOTHING""",
                (job.job_id, job.job_type.value, job.payload_id, job.status.value, job.attempts,
                 job.max_attempts, _iso(job.created_at), _iso(job.started_at), _iso(job.completed_at),
                 job.last_error, job.error_type, _iso(job.next_retry_at), _iso(job.lease_until), job.worker_id, job.stage),
            )
            row = connection.execute("SELECT * FROM background_jobs WHERE job_id = ?", (job.job_id,)).fetchone()
            return self._from_row(row)

    def get(self, job_id: str) -> Optional[BackgroundJob]:
        row = self._store._connection.execute("SELECT * FROM background_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._from_row(row) if row else None

    def claim(self, job_id: str, worker_id: str, now: datetime, lease_seconds: int) -> Optional[BackgroundJob]:
        with self._store.transaction() as connection:
            row = connection.execute("SELECT * FROM background_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None or row["status"] not in {BackgroundJobStatus.PENDING.value, BackgroundJobStatus.RETRY_WAITING.value}:
                return None
            retry_at = _parse(row["next_retry_at"])
            if retry_at and retry_at > now:
                return None
            updated = connection.execute(
                """UPDATE background_jobs SET status=?, started_at=?, worker_id=?, lease_until=?, attempts=attempts+1
                   WHERE job_id=? AND status IN (?, ?)""",
                (BackgroundJobStatus.RUNNING.value, _iso(now), worker_id,
                 _iso(now + timedelta(seconds=lease_seconds)), job_id,
                 BackgroundJobStatus.PENDING.value, BackgroundJobStatus.RETRY_WAITING.value),
            )
            if updated.rowcount != 1:
                return None
            return self._from_row(connection.execute("SELECT * FROM background_jobs WHERE job_id = ?", (job_id,)).fetchone())

    def transition(self, job_id: str, status: BackgroundJobStatus, now: datetime, **fields) -> BackgroundJob:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        if status not in _ALLOWED_TRANSITIONS[current.status]:
            raise InvalidJobTransition(f"{current.status.value} -> {status.value} is not allowed")
        values = {
            "status": status.value,
            "completed_at": _iso(now) if status in {BackgroundJobStatus.SUCCEEDED, BackgroundJobStatus.FAILED, BackgroundJobStatus.CANCELLED} else _iso(current.completed_at),
            "last_error": fields.get("last_error", current.last_error),
            "error_type": fields.get("error_type", current.error_type),
            "next_retry_at": _iso(fields.get("next_retry_at", current.next_retry_at)),
            "lease_until": None if status in {BackgroundJobStatus.SUCCEEDED, BackgroundJobStatus.FAILED, BackgroundJobStatus.CANCELLED} else _iso(fields.get("lease_until", current.lease_until)),
            "worker_id": fields.get("worker_id", current.worker_id),
        }
        with self._store.transaction() as connection:
            connection.execute(
                """UPDATE background_jobs SET status=?, completed_at=?, last_error=?, error_type=?,
                   next_retry_at=?, lease_until=?, worker_id=? WHERE job_id=?""",
                (values["status"], values["completed_at"], values["last_error"], values["error_type"],
                 values["next_retry_at"], values["lease_until"], values["worker_id"], job_id),
            )
        return self.get(job_id)

    def checkpoint(self, job_id: str, stage: str) -> BackgroundJob:
        with self._store.transaction() as connection:
            connection.execute("UPDATE background_jobs SET stage=? WHERE job_id=?", (stage, job_id))
        return self.get(job_id)

    def recover_stale(self, now: datetime) -> list[BackgroundJob]:
        stale = [job for job in self.list(BackgroundJobStatus.RUNNING) if job.lease_until and job.lease_until <= now]
        recovered = []
        for job in stale:
            if job.attempts < job.max_attempts:
                recovered.append(self.transition(job.job_id, BackgroundJobStatus.RETRY_WAITING, now, next_retry_at=now, worker_id=None))
            else:
                recovered.append(self.transition(job.job_id, BackgroundJobStatus.FAILED, now, last_error="lease expired", error_type="TRANSIENT"))
        return recovered

    def cancel(self, job_id: str, now: datetime) -> BackgroundJob:
        return self.transition(job_id, BackgroundJobStatus.CANCELLED, now)

    def list(self, status: Optional[BackgroundJobStatus] = None) -> list[BackgroundJob]:
        if status is None:
            rows = self._store._connection.execute("SELECT * FROM background_jobs ORDER BY job_id").fetchall()
        else:
            rows = self._store._connection.execute("SELECT * FROM background_jobs WHERE status=? ORDER BY job_id", (status.value,)).fetchall()
        return [self._from_row(row) for row in rows]
