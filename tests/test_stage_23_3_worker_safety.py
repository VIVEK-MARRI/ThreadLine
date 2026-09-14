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
from tests._clock_utils import MutableClock


def test_expired_worker_cannot_mutate_job_owned_by_recovered_worker(tmp_path):
    database = tmp_path / "source.db"
    clock = MutableClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    first = SQLiteBackgroundJobRepository(SQLiteSourceStore(database), clock)
    second = SQLiteBackgroundJobRepository(SQLiteSourceStore(database), clock)
    job = BackgroundJobScheduler(first).enqueue(BackgroundJobType.MEETING_PROCESSING, "meeting-1", clock())

    first.claim(job.job_id, "worker-a", lease_seconds=10)
    clock.advance(seconds=11)
    second.recover_stale()
    claimed = second.claim(job.job_id, "worker-b", lease_seconds=60)
    assert claimed is not None
    assert claimed.worker_id == "worker-b"
    assert claimed.attempts == 2

    with pytest.raises(StaleJobOwnershipError):
        first.checkpoint(job.job_id, "EXTRACTED", worker_id="worker-a")
    with pytest.raises(StaleJobOwnershipError):
        first.transition(
            job.job_id,
            BackgroundJobStatus.SUCCEEDED,
            owner_id="worker-a",
        )
    with pytest.raises(StaleJobOwnershipError):
        first.transition(
            job.job_id,
            BackgroundJobStatus.RETRY_WAITING,
            owner_id="worker-a",
        )

    second.checkpoint(job.job_id, "EXTRACTED", worker_id="worker-b")
    authoritative = second.get(job.job_id)
    assert authoritative.worker_id == "worker-b"
    assert authoritative.status == BackgroundJobStatus.RUNNING
    assert authoritative.stage == "EXTRACTED"


def test_sqlite_checkpoint_rejects_stage_regression(tmp_path):
    store = SQLiteSourceStore(tmp_path / "source.db")
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    repository = SQLiteBackgroundJobRepository(store, clock)
    job = BackgroundJobScheduler(repository).enqueue(BackgroundJobType.MEETING_PROCESSING, "meeting-1", clock())
    repository.claim(job.job_id, "worker-a", lease_seconds=60)
    repository.checkpoint(job.job_id, "SEMANTIC_INDEXED", worker_id="worker-a")

    with pytest.raises(InvalidJobTransition):
        repository.checkpoint(job.job_id, "RESOLVED", worker_id="worker-a")

    assert repository.get(job.job_id).stage == "SEMANTIC_INDEXED"
