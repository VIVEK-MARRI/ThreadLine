from datetime import datetime, timedelta, timezone

from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.background_job_repository import SQLiteBackgroundJobRepository
from app.services.background_worker_service import BackgroundJobScheduler, BackgroundWorkerService


def test_crashed_worker_job_is_recovered_by_new_worker(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repository = SQLiteBackgroundJobRepository(SQLiteSourceStore(tmp_path / "source.db"))
    scheduler = BackgroundJobScheduler(repository, max_attempts=3)
    job = scheduler.enqueue(BackgroundJobType.MEETING_PROCESSING, "m1", now - timedelta(minutes=2))
    repository.claim(job.job_id, "crashed-worker", now - timedelta(minutes=1), 10)
    worker = BackgroundWorkerService(repository, {BackgroundJobType.MEETING_PROCESSING: lambda _: None}, worker_id="new-worker")
    recovered = worker.recover(now)
    assert recovered[0].status == BackgroundJobStatus.RETRY_WAITING
    result = worker.run_once(now)
    assert result.status == BackgroundJobStatus.SUCCEEDED
    assert result.attempts == 2


def test_expired_lease_at_max_attempts_fails_safely(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repository = SQLiteBackgroundJobRepository(SQLiteSourceStore(tmp_path / "source.db"))
    scheduler = BackgroundJobScheduler(repository, max_attempts=1)
    job = scheduler.enqueue(BackgroundJobType.MEETING_PROCESSING, "m1", now - timedelta(minutes=2))
    repository.claim(job.job_id, "crashed-worker", now - timedelta(minutes=1), 10)
    recovered = repository.recover_stale(now)
    assert recovered[0].status == BackgroundJobStatus.FAILED
