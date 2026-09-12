from datetime import datetime, timezone

from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.models.meeting import Meeting
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.background_job_repository import SQLiteBackgroundJobRepository
from app.repositories.sqlite_source_repositories import SQLiteMeetingRepository
from app.services.background_worker_service import BackgroundJobScheduler, BackgroundWorkerService
from app.services.meeting_processing_service import MeetingProcessingService, ProcessingStage


def test_source_first_processing_pipeline_is_checkpointed(tmp_path):
    store = SQLiteSourceStore(tmp_path / "source.db")
    SQLiteMeetingRepository(store).save(Meeting(
        meeting_id="m1", title="Payments", transcript="Gateway approval", meeting_date=datetime.now(timezone.utc), ingested_at=datetime.now(timezone.utc)
    ))
    repository = SQLiteBackgroundJobRepository(store)
    scheduler = BackgroundJobScheduler(repository)
    scheduler.enqueue(BackgroundJobType.MEETING_PROCESSING, "m1")
    stages = []
    def process(job):
        assert SQLiteMeetingRepository(store).get_by_id(job.payload_id) is not None
        stages.extend(["EXTRACTED", "RELATIONSHIPS_PERSISTED", "SEMANTIC_INDEXED"])
    worker = BackgroundWorkerService(repository, {BackgroundJobType.MEETING_PROCESSING: process})
    result = worker.run_once()
    assert result.status == BackgroundJobStatus.SUCCEEDED
    assert stages == ["EXTRACTED", "RELATIONSHIPS_PERSISTED", "SEMANTIC_INDEXED"]
    assert SQLiteMeetingRepository(SQLiteSourceStore(tmp_path / "source.db")).get_by_id("m1") is not None


def test_semantic_failure_leaves_source_and_job_retryable(tmp_path):
    store = SQLiteSourceStore(tmp_path / "source.db")
    SQLiteMeetingRepository(store).save(Meeting(
        meeting_id="m1", title="Payments", transcript="Gateway approval", meeting_date=datetime.now(timezone.utc), ingested_at=datetime.now(timezone.utc)
    ))
    repository = SQLiteBackgroundJobRepository(store)
    scheduler = BackgroundJobScheduler(repository, max_attempts=2)
    scheduler.enqueue(BackgroundJobType.SEMANTIC_INDEXING, "m1")
    worker = BackgroundWorkerService(repository, {BackgroundJobType.SEMANTIC_INDEXING: lambda _: (_ for _ in ()).throw(RuntimeError("embedding unavailable"))})
    result = worker.run_once(datetime.now(timezone.utc))
    assert result.status == BackgroundJobStatus.RETRY_WAITING
    assert SQLiteMeetingRepository(SQLiteSourceStore(tmp_path / "source.db")).get_by_id("m1") is not None


def test_completed_processing_stages_are_skipped_on_retry():
    repository = SQLiteBackgroundJobRepository(SQLiteSourceStore(":memory:"))
    scheduler = BackgroundJobScheduler(repository)
    job = scheduler.enqueue(BackgroundJobType.MEETING_PROCESSING, "m1")
    repository.claim(job.job_id, "worker", datetime.now(timezone.utc), 60)
    repository.checkpoint(job.job_id, ProcessingStage.EXTRACTED.value)
    calls = []
    pipeline = MeetingProcessingService(
        repository,
        {stage: (lambda payload, stage=stage: calls.append(stage)) for stage in ProcessingStage if stage != ProcessingStage.COMPLETED},
    )
    pipeline.process(repository.get(job.job_id))
    assert ProcessingStage.EXTRACTED not in calls
    assert ProcessingStage.RESOLVED in calls
    assert repository.get(job.job_id).stage == ProcessingStage.COMPLETED.value
