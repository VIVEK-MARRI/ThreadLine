from datetime import datetime, timedelta, timezone

import pytest

from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.background_job_repository import (
    InvalidJobTransition,
    SQLiteBackgroundJobRepository,
    StaleJobOwnershipError,
)
from app.services.background_worker_service import BackgroundJobScheduler


def test_expired_worker_cannot_mutate_job_owned_by_recovered_worker(tmp_path):
    database = tmp_path / "source.db"
    first = SQLiteBackgroundJobRepository(SQLiteSourceStore(database))
    second = SQLiteBackgroundJobRepository(SQLiteSourceStore(database))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = BackgroundJobScheduler(first).enqueue(BackgroundJobType.MEETING_PROCESSING, "meeting-1", now)

    first.claim(job.job_id, "worker-a", now, lease_seconds=10)
    second.recover_stale(now + timedelta(seconds=11))
    claimed = second.claim(job.job_id, "worker-b", now + timedelta(seconds=11), lease_seconds=60)
    assert claimed is not None

    with pytest.raises(StaleJobOwnershipError):
        first.checkpoint(job.job_id, "EXTRACTED", worker_id="worker-a", now=now + timedelta(seconds=12))
    with pytest.raises(StaleJobOwnershipError):
        first.transition(
            job.job_id,
            BackgroundJobStatus.SUCCEEDED,
            now + timedelta(seconds=12),
            owner_id="worker-a",
        )
    with pytest.raises(StaleJobOwnershipError):
        first.transition(
            job.job_id,
            BackgroundJobStatus.RETRY_WAITING,
            now + timedelta(seconds=12),
            owner_id="worker-a",
            next_retry_at=now + timedelta(seconds=13),
        )

    second.checkpoint(job.job_id, "EXTRACTED", worker_id="worker-b", now=now + timedelta(seconds=12))
    authoritative = second.get(job.job_id)
    assert authoritative.worker_id == "worker-b"
    assert authoritative.status == BackgroundJobStatus.RUNNING
    assert authoritative.stage == "EXTRACTED"


def test_sqlite_checkpoint_rejects_stage_regression(tmp_path):
    store = SQLiteSourceStore(tmp_path / "source.db")
    repository = SQLiteBackgroundJobRepository(store)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = BackgroundJobScheduler(repository).enqueue(BackgroundJobType.MEETING_PROCESSING, "meeting-1", now)
    repository.claim(job.job_id, "worker-a", now, lease_seconds=60)
    repository.checkpoint(job.job_id, "SEMANTIC_INDEXED", worker_id="worker-a", now=now)

    with pytest.raises(InvalidJobTransition):
        repository.checkpoint(job.job_id, "RESOLVED", worker_id="worker-a", now=now)

    assert repository.get(job.job_id).stage == "SEMANTIC_INDEXED"
