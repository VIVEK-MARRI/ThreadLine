from datetime import datetime, timedelta, timezone

import pytest

from app.models.background_job import BackgroundJob, BackgroundJobStatus, BackgroundJobType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.background_job_repository import (
    InMemoryBackgroundJobRepository,
    InvalidJobTransition,
    SQLiteBackgroundJobRepository,
)


def make_job(payload="m1", created=None):
    return BackgroundJob(
        job_id=f"MEETING_PROCESSING:{payload}", job_type=BackgroundJobType.MEETING_PROCESSING,
        payload_id=payload, created_at=created or datetime.now(timezone.utc), max_attempts=3,
    )


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    if request.param == "memory":
        return InMemoryBackgroundJobRepository()
    return SQLiteBackgroundJobRepository(SQLiteSourceStore(tmp_path / "source.db"))


def test_enqueue_is_idempotent(repository):
    first = repository.enqueue(make_job())
    second = repository.enqueue(make_job())
    assert first.job_id == second.job_id
    assert len(repository.list()) == 1


def test_claim_is_atomic_and_only_one_claim_wins(repository):
    repository.enqueue(make_job())
    now = datetime.now(timezone.utc)
    first = repository.claim("MEETING_PROCESSING:m1", "worker-a", now, 30)
    second = repository.claim("MEETING_PROCESSING:m1", "worker-b", now, 30)
    assert first.worker_id == "worker-a"
    assert second is None


def test_invalid_transition_is_rejected(repository):
    repository.enqueue(make_job())
    with pytest.raises(InvalidJobTransition):
        repository.transition("MEETING_PROCESSING:m1", BackgroundJobStatus.SUCCEEDED, datetime.now(timezone.utc))


def test_checkpoint_is_persisted(repository):
    repository.enqueue(make_job())
    now = datetime.now(timezone.utc)
    repository.claim("MEETING_PROCESSING:m1", "worker-a", now, 30)
    repository.checkpoint("MEETING_PROCESSING:m1", "EXTRACTED")
    assert repository.get("MEETING_PROCESSING:m1").stage == "EXTRACTED"


def test_cancel_only_works_before_claim(repository):
    repository.enqueue(make_job())
    repository.cancel("MEETING_PROCESSING:m1", datetime.now(timezone.utc))
    assert repository.get("MEETING_PROCESSING:m1").status == BackgroundJobStatus.CANCELLED
    assert repository.claim("MEETING_PROCESSING:m1", "worker-a", datetime.now(timezone.utc), 30) is None


def test_stale_running_job_becomes_retryable(repository):
    created = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository.enqueue(make_job(created=created))
    started = created + timedelta(seconds=1)
    repository.claim("MEETING_PROCESSING:m1", "dead-worker", started, 1)
    recovered = repository.recover_stale(started + timedelta(seconds=2))
    assert recovered[0].status == BackgroundJobStatus.RETRY_WAITING
    assert repository.get("MEETING_PROCESSING:m1").next_retry_at == started + timedelta(seconds=2)


def test_sqlite_repository_survives_recreation(tmp_path):
    path = tmp_path / "source.db"
    first = SQLiteBackgroundJobRepository(SQLiteSourceStore(path))
    first.enqueue(make_job())
    second = SQLiteBackgroundJobRepository(SQLiteSourceStore(path))
    assert second.get("MEETING_PROCESSING:m1").payload_id == "m1"
