from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.services.background_job_service import BackgroundJobService


def test_job_is_idempotent_by_type_and_payload():
    calls = []
    service = BackgroundJobService({BackgroundJobType.SEMANTIC_INDEXING: lambda item: calls.append(item)})
    first = service.create_job(BackgroundJobType.SEMANTIC_INDEXING, "e1")
    second = service.create_job(BackgroundJobType.SEMANTIC_INDEXING, "e1")
    assert first.job_id == second.job_id
    assert service.run(first.job_id).status == BackgroundJobStatus.COMPLETED
    assert service.run(first.job_id).attempts == 1
    assert calls == ["e1"]


def test_job_retries_with_bounded_attempts():
    attempts = []
    def failing(_):
        attempts.append(1)
        raise RuntimeError("provider unavailable")
    service = BackgroundJobService({BackgroundJobType.MEETING_PROCESSING: failing}, max_attempts=2)
    job = service.create_job(BackgroundJobType.MEETING_PROCESSING, "m1")
    result = service.run(job.job_id)
    assert result.status == BackgroundJobStatus.FAILED
    assert result.attempts == 2
    assert len(attempts) == 2
    assert result.error == "provider unavailable"


def test_retry_can_succeed_without_duplicate_job():
    attempts = []
    def eventually_succeeds(_):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary")
    service = BackgroundJobService({BackgroundJobType.DERIVED_INTELLIGENCE_REBUILD: eventually_succeeds}, max_attempts=3)
    job = service.create_job(BackgroundJobType.DERIVED_INTELLIGENCE_REBUILD, "e1")
    result = service.run(job.job_id)
    assert result.status == BackgroundJobStatus.COMPLETED
    assert result.attempts == 2
