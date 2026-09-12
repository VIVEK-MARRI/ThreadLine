from datetime import datetime, timezone

import pytest

from app.models.background_job import BackgroundJob, BackgroundJobType
from app.repositories.background_job_repository import InMemoryBackgroundJobRepository
from app.schemas.meeting import MeetingIngestRequest
from app.services.background_worker_service import BackgroundWorkerService, PermanentJobError
from app.services.meeting_processing_service import MeetingProcessingService
from app.services.meeting_service import MeetingConflictError, MeetingService
from app.repositories.meeting_repository import InMemoryMeetingRepository


def request(title="Payments", transcript="Gateway approval"):
    return MeetingIngestRequest(meeting_id="m-e2e-001", title=title, transcript=transcript, meeting_date=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_same_meeting_payload_is_idempotent():
    service = MeetingService(InMemoryMeetingRepository())
    first = service.ingest_meeting(request())
    second = service.ingest_meeting(request())
    assert first.meeting_id == second.meeting_id
    assert first.ingested_at == second.ingested_at


def test_same_meeting_id_conflicting_payload_is_explicit():
    service = MeetingService(InMemoryMeetingRepository())
    service.ingest_meeting(request())
    with pytest.raises(MeetingConflictError):
        service.ingest_meeting(request(transcript="Different transcript"))


def test_missing_stage_handler_fails_without_checkpoint():
    repository = InMemoryBackgroundJobRepository()
    job = repository.enqueue(BackgroundJob(job_id="MEETING_PROCESSING:m1", job_type=BackgroundJobType.MEETING_PROCESSING, payload_id="m1", created_at=datetime.now(timezone.utc)))
    worker = BackgroundWorkerService(repository, {BackgroundJobType.MEETING_PROCESSING: lambda current: MeetingProcessingService(repository, {}).process(current)})
    result = worker.run_once(datetime.now(timezone.utc))
    assert result.status == "FAILED"
    assert result.stage is None
