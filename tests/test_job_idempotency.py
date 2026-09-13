from datetime import datetime, timezone

from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.repositories.background_job_repository import InMemoryBackgroundJobRepository
from app.services.background_worker_service import BackgroundJobScheduler, BackgroundWorkerService


def test_ten_enqueue_requests_create_one_logical_job():
    repository = InMemoryBackgroundJobRepository()
    scheduler = BackgroundJobScheduler(repository)
    jobs = [scheduler.enqueue(BackgroundJobType.MEETING_PROCESSING, "m1") for _ in range(10)]
    assert len({job.job_id for job in jobs}) == 1
    assert len(repository.list()) == 1


def test_retry_does_not_duplicate_execution_after_success():
    repository = InMemoryBackgroundJobRepository()
    scheduler = BackgroundJobScheduler(repository, max_attempts=3)
    scheduler.enqueue(BackgroundJobType.SEMANTIC_INDEXING, "e1", datetime(2026, 1, 1, tzinfo=timezone.utc))
    calls = []
    worker = BackgroundWorkerService(repository, {BackgroundJobType.SEMANTIC_INDEXING: lambda job: calls.append(job.payload_id)})
    worker.run_once(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert worker.run_once(datetime(2026, 1, 1, tzinfo=timezone.utc)) is None
    assert calls == ["e1"]
    assert repository.list()[0].status == BackgroundJobStatus.SUCCEEDED


def test_new_processing_revision_gets_a_new_durable_job():
    repository = InMemoryBackgroundJobRepository()
    scheduler = BackgroundJobScheduler(repository)

    first = scheduler.enqueue(
        BackgroundJobType.MEETING_PROCESSING,
        "meeting-1",
        processing_revision="revision-1",
    )
    second = scheduler.enqueue(
        BackgroundJobType.MEETING_PROCESSING,
        "meeting-1",
        processing_revision="revision-2",
    )

    assert first.job_id != second.job_id
    assert len(repository.list()) == 2
