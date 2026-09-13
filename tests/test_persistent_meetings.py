from datetime import datetime, timezone

from app.models.meeting import Meeting
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.sqlite_source_repositories import SQLiteMeetingRepository


def meeting(meeting_id="m1"):
    return Meeting(
        meeting_id=meeting_id,
        title="Payments",
        transcript="Gateway approval is pending.",
        meeting_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        participants=["Priya"],
        ingested_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        idempotency_key=meeting_id,
    )


def test_meeting_survives_repository_recreation(tmp_path):
    path = tmp_path / "source.db"
    store = SQLiteSourceStore(path)
    SQLiteMeetingRepository(store).save(meeting())
    store.close()
    restored_store = SQLiteSourceStore(path)
    restored = SQLiteMeetingRepository(restored_store).get_by_id("m1")
    assert restored is not None
    assert restored.transcript == "Gateway approval is pending."


def test_meeting_upsert_preserves_identity(tmp_path):
    repository = SQLiteMeetingRepository(SQLiteSourceStore(tmp_path / "source.db"))
    repository.save(meeting())
    # Source mutations must advance the durable revision: same-revision
    # overwrites with different payload are rejected as concurrent conflicts
    # (see monotonic guard), so the update carries revision 2.
    updated = meeting().model_copy(update={"title": "Payments Review", "source_revision": 2})
    repository.save(updated)
    assert repository.get_by_id("m1").title == "Payments Review"
