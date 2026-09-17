"""Durable and in-memory repositories for background job state.

Time source
-----------
All lease validation and timestamp persistence is driven by an injectable
clock (`Callable[[], datetime]`), never by caller-supplied timestamps.  This
closes the defect class where a worker passes a stale or pre-handler snapshot
of `datetime.now()` into a durable mutation and thereby extends its lease,
checkpoints a job after its lease expired, or freezes a job in RETRY_WAITING
with an old `next_retry_at`.

The repository is the single authority for "when is now":
  - claim/checkpoint/transition/cancel/recover_stale always consult
    `self._clock()` at call time.
  - tests inject a controlled clock to simulate lease expiry and recovery
    without threading fake timestamps through every method.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from app.models.background_job import BackgroundJob, BackgroundJobStatus, BackgroundJobType
from app.persistence.sqlite_store import SQLiteSourceStore


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


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

_STAGE_ORDER = {
    None: -1,
    "INGESTED": 0,
    "EXTRACTED": 1,
    "RESOLVED": 2,
    "RELATIONSHIPS_PERSISTED": 3,
    "DERIVED_INTELLIGENCE": 4,
    "SEMANTIC_INDEXED": 5,
    "COMPLETED": 6,
}


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class InvalidJobTransition(ValueError):
    """Raised when a job state transition is not allowed."""


class StaleJobOwnershipError(InvalidJobTransition):
    """Raised when a worker mutates a job after losing its lease."""


class AbstractBackgroundJobRepository(ABC):
    @abstractmethod
    def enqueue(self, job: BackgroundJob) -> BackgroundJob:
        """Create a job once, returning the existing logical job if present."""
        ...

    @abstractmethod
    def get(self, job_id: str) -> Optional[BackgroundJob]:
        ...

    @abstractmethod
    def claim(self, job_id: str, worker_id: str, lease_seconds: int) -> Optional[BackgroundJob]:
        """Atomically transition an eligible job to RUNNING.

        The lease window is computed from the repository clock at call time.
        """
        ...

    @abstractmethod
    def transition(self, job_id: str, status: BackgroundJobStatus, **fields) -> BackgroundJob:
        ...

    @abstractmethod
    def checkpoint(self, job_id: str, stage: str, worker_id: Optional[str] = None) -> BackgroundJob:
        ...

    @abstractmethod
    def record_scan_result(
        self,
        job_id: str,
        result_summary: str,
        processing_revision: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> BackgroundJob:
        """Persist a scan handler's durable result on a RUNNING job.

        Stage 34: ORGANISATION_INTELLIGENCE_SCAN handlers call this before
        returning success so the JSON summary (observed source watermark,
        deterministic signal IDs, new-signal IDs) survives in the job row.
        Ownership is enforced exactly like checkpoint when worker_id is
        given; processing_revision carries the observed source watermark.
        """
        ...

    @abstractmethod
    def recover_stale(self) -> list[BackgroundJob]:
        ...

    @abstractmethod
    def cancel(self, job_id: str) -> BackgroundJob:
        ...

    @abstractmethod
    def list(
        self,
        status: Optional[BackgroundJobStatus] = None,
        organisation_id: Optional[str] = None,
    ) -> list[BackgroundJob]:
        ...

    def counts(self, organisation_id: Optional[str] = None) -> dict[str, int]:
        return {
            status.value: len(self.list(status, organisation_id))
            for status in BackgroundJobStatus
            if status.name != "COMPLETED"
        }

    def oldest_pending_age_seconds(self, now: datetime) -> Optional[float]:
        jobs = self.list(BackgroundJobStatus.PENDING)
        if not jobs:
            return None
        return max(0.0, (now - min(job.created_at for job in jobs)).total_seconds())


class InMemoryBackgroundJobRepository(AbstractBackgroundJobRepository):
    def __init__(self, clock: Optional[Callable[[], datetime]] = None) -> None:
        from threading import RLock
        self._jobs: dict[str, BackgroundJob] = {}
        self._lock = RLock()
        self._clock = clock or _default_clock

    def enqueue(self, job: BackgroundJob) -> BackgroundJob:
        with self._lock:
            return self._jobs.setdefault(job.job_id, job)

    def get(self, job_id: str) -> Optional[BackgroundJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def claim(self, job_id: str, worker_id: str, lease_seconds: int) -> Optional[BackgroundJob]:
        now = self._clock()
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

    def transition(self, job_id: str, status: BackgroundJobStatus, **fields) -> BackgroundJob:
        now = self._clock()
        with self._lock:
            job = self._jobs[job_id]
            if status not in _ALLOWED_TRANSITIONS[job.status]:
                raise InvalidJobTransition(f"{job.status.value} -> {status.value} is not allowed")
            owner = fields.get("owner_id")
            if owner is not None and (
                job.status != BackgroundJobStatus.RUNNING
                or job.worker_id != owner
                or job.lease_until is None
                or job.lease_until <= now
            ):
                raise StaleJobOwnershipError(f"worker {owner} no longer owns job {job_id}")
            fields = {key: value for key, value in fields.items() if key != "owner_id"}
            job.status = status
            for key, value in fields.items():
                setattr(job, key, value)
            if status in {BackgroundJobStatus.SUCCEEDED, BackgroundJobStatus.FAILED, BackgroundJobStatus.CANCELLED}:
                job.completed_at = now
                job.lease_until = None
            return job

    def checkpoint(self, job_id: str, stage: str, worker_id: Optional[str] = None) -> BackgroundJob:
        now = self._clock()
        with self._lock:
            job = self._jobs[job_id]
            if _STAGE_ORDER.get(stage, -1) < _STAGE_ORDER.get(job.stage, -1):
                raise InvalidJobTransition(f"checkpoint regression: {job.stage} -> {stage}")
            if worker_id is not None and (
                job.status != BackgroundJobStatus.RUNNING
                or job.worker_id != worker_id
                or job.lease_until is None
                or job.lease_until <= now
            ):
                raise StaleJobOwnershipError(f"worker {worker_id} no longer owns job {job_id}")
            job.stage = stage
            return job

    def record_scan_result(
        self,
        job_id: str,
        result_summary: str,
        processing_revision: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> BackgroundJob:
        now = self._clock()
        with self._lock:
            job = self._jobs[job_id]
            if worker_id is not None and (
                job.status != BackgroundJobStatus.RUNNING
                or job.worker_id != worker_id
                or job.lease_until is None
                or job.lease_until <= now
            ):
                raise StaleJobOwnershipError(f"worker {worker_id} no longer owns job {job_id}")
            job.result_summary = result_summary
            if processing_revision is not None:
                job.processing_revision = processing_revision
            return job

    def recover_stale(self) -> list[BackgroundJob]:
        now = self._clock()
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

    def cancel(self, job_id: str) -> BackgroundJob:
        return self.transition(job_id, BackgroundJobStatus.CANCELLED)

    def list(
        self,
        status: Optional[BackgroundJobStatus] = None,
        organisation_id: Optional[str] = None,
    ) -> list[BackgroundJob]:
        with self._lock:
            jobs = list(self._jobs.values())
            if status is not None:
                jobs = [job for job in jobs if job.status == status]
            if organisation_id is not None:
                jobs = [job for job in jobs if job.organisation_id == organisation_id]
            return sorted(jobs, key=lambda job: job.job_id)


class SQLiteBackgroundJobRepository(AbstractBackgroundJobRepository):
    def __init__(self, store: SQLiteSourceStore, clock: Optional[Callable[[], datetime]] = None) -> None:
        self._store = store
        self._clock = clock or _default_clock

    @staticmethod
    def _from_row(row) -> BackgroundJob:
        try:
            organisation_id = row["organisation_id"]
        except Exception:
            organisation_id = None
        try:
            result_summary = row["result_summary"]
        except Exception:
            # Pre-Stage-34 rows (schema v5): no summary column yet.
            result_summary = None
        return BackgroundJob(
            job_id=row["job_id"], job_type=row["job_type"], payload_id=row["payload_id"],
            status=row["status"], attempts=row["attempts"], max_attempts=row["max_attempts"],
            created_at=_parse(row["created_at"]), started_at=_parse(row["started_at"]),
            completed_at=_parse(row["completed_at"]), last_error=row["last_error"],
            error_type=row["error_type"], next_retry_at=_parse(row["next_retry_at"]),
            lease_until=_parse(row["lease_until"]), worker_id=row["worker_id"], stage=row["stage"],
            processing_revision=row["processing_revision"],
            result_summary=result_summary,
            organisation_id=organisation_id or "default",
        )

    def enqueue(self, job: BackgroundJob) -> BackgroundJob:
        with self._store.transaction() as connection:
            connection.execute(
                """INSERT INTO background_jobs
                (job_id, job_type, payload_id, status, attempts, max_attempts, created_at,
                 started_at, completed_at, last_error, error_type, next_retry_at, lease_until, worker_id, stage, processing_revision, result_summary, organisation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO NOTHING""",
                (job.job_id, job.job_type.value, job.payload_id, job.status.value, job.attempts,
                 job.max_attempts, _iso(job.created_at), _iso(job.started_at), _iso(job.completed_at),
                 job.last_error, job.error_type, _iso(job.next_retry_at), _iso(job.lease_until), job.worker_id, job.stage, job.processing_revision, job.result_summary, job.organisation_id),
            )
            row = connection.execute("SELECT * FROM background_jobs WHERE job_id = ?", (job.job_id,)).fetchone()
            return self._from_row(row)

    def get(self, job_id: str) -> Optional[BackgroundJob]:
        row = self._store._connection.execute("SELECT * FROM background_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._from_row(row) if row else None

    def claim(self, job_id: str, worker_id: str, lease_seconds: int) -> Optional[BackgroundJob]:
        now = self._clock()
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

    def transition(self, job_id: str, status: BackgroundJobStatus, **fields) -> BackgroundJob:
        now = self._clock()
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        if status not in _ALLOWED_TRANSITIONS[current.status]:
            raise InvalidJobTransition(f"{current.status.value} -> {status.value} is not allowed")
        owner = fields.get("owner_id")
        if owner is not None and (
            current.status != BackgroundJobStatus.RUNNING
            or current.worker_id != owner
            or current.lease_until is None
            or current.lease_until <= now
        ):
            raise StaleJobOwnershipError(f"worker {owner} no longer owns job {job_id}")
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
            updated = connection.execute(
                """UPDATE background_jobs SET status=?, completed_at=?, last_error=?, error_type=?,
                   next_retry_at=?, lease_until=?, worker_id=? WHERE job_id=? AND status=?
                   AND (? IS NULL OR (worker_id=? AND lease_until IS NOT NULL AND lease_until>?))""",
                (values["status"], values["completed_at"], values["last_error"], values["error_type"],
                 values["next_retry_at"], values["lease_until"], values["worker_id"], job_id, current.status.value,
                 owner, owner, _iso(now)),
            )
            if updated.rowcount != 1:
                raise StaleJobOwnershipError(f"worker {owner} no longer owns job {job_id}") if owner else InvalidJobTransition("job state changed before transition")
        return self.get(job_id)

    def checkpoint(self, job_id: str, stage: str, worker_id: Optional[str] = None) -> BackgroundJob:
        now = self._clock()
        with self._store.transaction() as connection:
            row = connection.execute("SELECT stage FROM background_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if _STAGE_ORDER.get(stage, -1) < _STAGE_ORDER.get(row["stage"], -1):
                raise InvalidJobTransition(f"checkpoint regression: {row['stage']} -> {stage}")
            updated = connection.execute(
                """UPDATE background_jobs SET stage=? WHERE job_id=?
                   AND (? IS NULL OR (status=? AND worker_id=? AND lease_until IS NOT NULL AND lease_until>?))""",
                (stage, job_id, worker_id, BackgroundJobStatus.RUNNING.value, worker_id, _iso(now)),
            )
            if updated.rowcount != 1:
                raise StaleJobOwnershipError(f"worker {worker_id} no longer owns job {job_id}")
        return self.get(job_id)

    def record_scan_result(
        self,
        job_id: str,
        result_summary: str,
        processing_revision: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> BackgroundJob:
        now = self._clock()
        with self._store.transaction() as connection:
            row = connection.execute("SELECT * FROM background_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if worker_id is not None and (
                row["status"] != BackgroundJobStatus.RUNNING.value
                or row["worker_id"] != worker_id
                or row["lease_until"] is None
                or _parse(row["lease_until"]) is None
                or _parse(row["lease_until"]) <= now
            ):
                raise StaleJobOwnershipError(f"worker {worker_id} no longer owns job {job_id}")
            revision = processing_revision if processing_revision is not None else row["processing_revision"]
            updated = connection.execute(
                """UPDATE background_jobs SET result_summary=?, processing_revision=? WHERE job_id=?
                   AND (? IS NULL OR (status=? AND worker_id=? AND lease_until IS NOT NULL AND lease_until>?))""",
                (result_summary, revision, job_id, worker_id, BackgroundJobStatus.RUNNING.value, worker_id, _iso(now)),
            )
            if updated.rowcount != 1:
                raise StaleJobOwnershipError(f"worker {worker_id} no longer owns job {job_id}")
        return self.get(job_id)

    def recover_stale(self) -> list[BackgroundJob]:
        now = self._clock()
        stale = [job for job in self.list(BackgroundJobStatus.RUNNING) if job.lease_until and job.lease_until <= now]
        recovered = []
        for job in stale:
            if job.attempts < job.max_attempts:
                recovered.append(self.transition(job.job_id, BackgroundJobStatus.RETRY_WAITING, next_retry_at=now, worker_id=None))
            else:
                recovered.append(self.transition(job.job_id, BackgroundJobStatus.FAILED, last_error="lease expired", error_type="TRANSIENT"))
        return recovered

    def cancel(self, job_id: str) -> BackgroundJob:
        return self.transition(job_id, BackgroundJobStatus.CANCELLED)

    def list(
        self,
        status: Optional[BackgroundJobStatus] = None,
        organisation_id: Optional[str] = None,
    ) -> list[BackgroundJob]:
        clauses = []
        args: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            args.append(status.value)
        if organisation_id is not None:
            clauses.append("organisation_id = ?")
            args.append(organisation_id)
        query = "SELECT * FROM background_jobs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY job_id"
        rows = self._store._connection.execute(query, tuple(args)).fetchall()
        return [self._from_row(row) for row in rows]
