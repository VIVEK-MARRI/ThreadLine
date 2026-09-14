from datetime import datetime, timedelta, timezone

import pytest

from app.models.background_job import BackgroundJob, BackgroundJobStatus, BackgroundJobType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.background_job_repository import (
    InMemoryBackgroundJobRepository,
    InvalidJobTransition,
    SQLiteBackgroundJobRepository,
)
from tests._clock_utils import MutableClock


def make_job(payload="m1", created=None):
    return BackgroundJob(
        job_id=f"MEETING_PROCESSING:{payload}", job_type=BackgroundJobType.MEETING_PROCESSING,
        payload_id=payload, created_at=created or datetime.now(timezone.utc), max_attempts=3,
    )


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    clock = MutableClock()
    if request.param == "memory":
        return InMemoryBackgroundJobRepository(clock), clock
    return SQLiteBackgroundJobRepository(SQLiteSourceStore(tmp_path / "source.db"), clock), clock


def test_enqueue_is_idempotent(repository):
    repo, _ = repository
    first = repo.enqueue(make_job())
    second = repo.enqueue(make_job())
    assert first.job_id == second.job_id
    assert len(repo.list()) == 1


def test_claim_is_atomic_and_only_one_claim_wins(repository):
    repo, _ = repository
    repo.enqueue(make_job())
    first = repo.claim("MEETING_PROCESSING:m1", "worker-a", 30)
    second = repo.claim("MEETING_PROCESSING:m1", "worker-b", 30)
    assert first.worker_id == "worker-a"
    assert second is None
    assert first.lease_until == first.started_at + timedelta(seconds=30)


def test_invalid_transition_is_rejected(repository):
    repo, _ = repository
    repo.enqueue(make_job())
    with pytest.raises(InvalidJobTransition):
        repo.transition("MEETING_PROCESSING:m1", BackgroundJobStatus.SUCCEEDED)


def test_checkpoint_is_persisted(repository):
    repo, _ = repository
    repo.enqueue(make_job())
    repo.claim("MEETING_PROCESSING:m1", "worker-a", 30)
    repo.checkpoint("MEETING_PROCESSING:m1", "EXTRACTED")
    assert repo.get("MEETING_PROCESSING:m1").stage == "EXTRACTED"


def test_cancel_only_works_before_claim(repository):
    repo, clock = repository
    repo.enqueue(make_job())
    repo.cancel("MEETING_PROCESSING:m1")
    assert repo.get("MEETING_PROCESSING:m1").status == BackgroundJobStatus.CANCELLED
    assert repo.claim("MEETING_PROCESSING:m1", "worker-a", 30) is None
    with pytest.raises(InvalidJobTransition):
        repo.transition("MEETING_PROCESSING:m1", BackgroundJobStatus.SUCCEEDED, owner_id="worker-a")
    clock.advance(minutes=1)
    assert repo.get("MEETING_PROCESSING:m1").completed_at == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_stale_running_job_becomes_retryable(repository):
    repo, clock = repository
    created = clock()
    repo.enqueue(make_job(created=created))
    repo.claim("MEETING_PROCESSING:m1", "dead-worker", 1)
    clock.advance(seconds=2)
    recovered = repo.recover_stale()
    assert recovered[0].status == BackgroundJobStatus.RETRY_WAITING
    assert repo.get("MEETING_PROCESSING:m1").next_retry_at == datetime(2026, 1, 1, 12, 0, 2, tzinfo=timezone.utc)


def test_sqlite_repository_survives_recreation(tmp_path):
    path = tmp_path / "source.db"
    first = SQLiteBackgroundJobRepository(SQLiteSourceStore(path))
    first.enqueue(make_job())
    second = SQLiteBackgroundJobRepository(SQLiteSourceStore(path))
    assert second.get("MEETING_PROCESSING:m1").payload_id == "m1"