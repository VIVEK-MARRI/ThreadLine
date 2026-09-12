from datetime import datetime, timezone

import pytest

from app.extraction.base import ExtractionError
from app.models.meeting import Meeting
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.sqlite_source_repositories import SQLiteExtractionRepository, SQLiteMeetingRepository
from app.schemas.meeting import MeetingIngestRequest
from app.services.extraction_service import ExtractionService
from app.services.meeting_service import MeetingService


class FailingProvider:
    def extract(self, transcript: str, meeting_id: str):
        raise ExtractionError("provider unavailable")


def test_ingestion_persists_source_before_provider_failure(tmp_path):
    store = SQLiteSourceStore(tmp_path / "source.db")
    meeting_service = MeetingService(SQLiteMeetingRepository(store))
    meeting = meeting_service.ingest_meeting(MeetingIngestRequest(
        meeting_id="meeting-1",
        title="Payments",
        transcript="Gateway approval is pending.",
        meeting_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    extraction_service = ExtractionService(
        meeting_repository=SQLiteMeetingRepository(store),
        extraction_repository=SQLiteExtractionRepository(store),
        provider=FailingProvider(),
    )
    with pytest.raises(ExtractionError):
        extraction_service.extract_meeting(meeting.meeting_id)
    restored = SQLiteMeetingRepository(SQLiteSourceStore(tmp_path / "source.db")).get_by_id("meeting-1")
    assert restored is not None
    assert SQLiteExtractionRepository(SQLiteSourceStore(tmp_path / "source.db")).get_by_meeting_id("meeting-1") is None


def test_reingestion_with_same_meeting_id_is_idempotent(tmp_path):
    repository = SQLiteMeetingRepository(SQLiteSourceStore(tmp_path / "source.db"))
    service = MeetingService(repository)
    request = MeetingIngestRequest(
        meeting_id="meeting-1", title="Payments", transcript="Gateway", meeting_date=datetime.now(timezone.utc)
    )
    first = service.ingest_meeting(request)
    second = service.ingest_meeting(request)
    assert first.meeting_id == second.meeting_id == "meeting-1"
    assert repository.get_by_id("meeting-1").meeting_id == "meeting-1"