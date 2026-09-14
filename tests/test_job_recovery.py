from datetime import datetime, timezone

from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.background_job_repository import SQLiteBackgroundJobRepository
from app.services.background_worker_service import BackgroundJobScheduler, BackgroundWorkerService
from tests._clock_utils import MutableClock


def test_crashed_worker_job_is_recovered_by_new_worker(tmp_path):
    clock = MutableClock(datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc))
    repository = SQLiteBackgroundJobRepository(SQLiteSourceStore(tmp_path / "source.db"), clock)
    scheduler = BackgroundJobScheduler(repository, max_attempts=3)
    job = scheduler.enqueue(BackgroundJobType.MEETING_PROCESSING, "m1")
    claim_time = clock.advance(minutes=1)
    repository.claim(job.job_id, "crashed-worker", 10)
    recover_time = clock.advance(seconds=11)
    worker = BackgroundWorkerService(
        repository,
        {BackgroundJobType.MEETING_PROCESSING: lambda _: None},
        worker_id="new-worker",
        clock=clock,
    )
    recovered = worker.recover()
    assert recovered[0].status == BackgroundJobStatus.RETRY_WAITING
    assert recovered[0].next_retry_at == recover_time
    result = worker.run_once()
    assert result.status == BackgroundJobStatus.SUCCEEDED
    assert result.attempts == 2


def test_expired_lease_at_max_attempts_fails_safely(tmp_path):
    clock = MutableClock(datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc))
    repository = SQLiteBackgroundJobRepository(SQLiteSourceStore(tmp_path / "source.db"), clock)
    scheduler = BackgroundJobScheduler(repository, max_attempts=1)
    job = scheduler.enqueue(BackgroundJobType.MEETING_PROCESSING, "m1")
    clock.advance(minutes=1)
    repository.claim(job.job_id, "crashed-worker", 10)
    clock.advance(seconds=11)
    recovered = repository.recover_stale()
    assert recovered[0].status == BackgroundJobStatus.FAILED