from datetime import datetime, timedelta, timezone

from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.repositories.background_job_repository import InMemoryBackgroundJobRepository
from app.services.background_worker_service import (
    BackgroundJobScheduler,
    BackgroundWorkerService,
    PermanentJobError,
    TransientJobError,
)
from tests._clock_utils import MutableClock


def test_worker_executes_and_checkpoints_job():
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    repository = InMemoryBackgroundJobRepository(clock)
    scheduler = BackgroundJobScheduler(repository, max_attempts=3)
    job = scheduler.enqueue(BackgroundJobType.MEETING_PROCESSING, "m1", clock())
    seen = []
    worker = BackgroundWorkerService(
        repository,
        {BackgroundJobType.MEETING_PROCESSING: lambda current: seen.append(current.payload_id)},
        worker_id="worker-a",
        clock=clock,
    )
    result = worker.run_once(clock())
    assert result.status == BackgroundJobStatus.SUCCEEDED
    assert result.completed_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert seen == ["m1"]
    assert worker.checkpoint(job.job_id, "COMPLETED").stage == "COMPLETED"


def test_transient_error_uses_injected_backoff_without_sleep():
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    repository = InMemoryBackgroundJobRepository(clock)
    scheduler = BackgroundJobScheduler(repository, max_attempts=2)
    job = scheduler.enqueue(BackgroundJobType.SEMANTIC_INDEXING, "e1", clock())
    worker = BackgroundWorkerService(
        repository,
        {BackgroundJobType.SEMANTIC_INDEXING: lambda _: (_ for _ in ()).throw(TransientJobError("busy"))},
        worker_id="worker-a",
        backoff_seconds=5,
        clock=clock,
    )
    result = worker.run_once(clock())
    assert result.status == BackgroundJobStatus.RETRY_WAITING
    assert result.next_retry_at == datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc)
    assert repository.get(job.job_id).attempts == 1


def test_permanent_error_does_not_retry():
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    repository = InMemoryBackgroundJobRepository(clock)
    scheduler = BackgroundJobScheduler(repository, max_attempts=3)
    scheduler.enqueue(BackgroundJobType.MEETING_PROCESSING, "bad", clock())
    worker = BackgroundWorkerService(
        repository,
        {BackgroundJobType.MEETING_PROCESSING: lambda _: (_ for _ in ()).throw(PermanentJobError("invalid"))},
        worker_id="worker-a",
        clock=clock,
    )
    result = worker.run_once(clock())
    assert result.status == BackgroundJobStatus.FAILED
    assert result.completed_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert result.attempts == 1
    assert result.error_type == "PERMANENT"


def test_shutdown_stops_new_claims():
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    repository = InMemoryBackgroundJobRepository(clock)
    scheduler = BackgroundJobScheduler(repository)
    scheduler.enqueue(BackgroundJobType.MEETING_PROCESSING, "m1")
    worker = BackgroundWorkerService(repository, {}, worker_id="worker-a", clock=clock)
    worker.shutdown()
    assert worker.run_once() is None