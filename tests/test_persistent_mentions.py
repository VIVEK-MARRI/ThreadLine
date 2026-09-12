from datetime import datetime, timezone

from app.models.entity import EntityMention, EntityType, ResolutionStatus
from app.models.meeting import Meeting
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.sqlite_source_repositories import SQLiteEntityRepository, SQLiteMeetingRepository, SQLiteMentionRepository


def test_mention_survives_restart_without_recalculation(tmp_path):
    store = SQLiteSourceStore(tmp_path / "source.db")
    SQLiteMeetingRepository(store).save(Meeting(
        meeting_id="m1", title="Payments", transcript="Gateway", meeting_date=datetime.now(timezone.utc), ingested_at=datetime.now(timezone.utc)
    ))
    mention = EntityMention(
        mention_id="mention-1", entity_type=EntityType.ISSUE, text="gateway", meeting_id="m1",
        source_text="Gateway approval is pending.", entity_id=None,
        resolution_status=ResolutionStatus.AMBIGUOUS, created_at=datetime.now(timezone.utc),
    )
    SQLiteMentionRepository(store).create(mention)
    restored = SQLiteMentionRepository(SQLiteSourceStore(tmp_path / "source.db")).get_by_id("mention-1")
    assert restored.resolution_status == ResolutionStatus.AMBIGUOUS
    assert restored.entity_id is None


def test_mentions_are_queryable_by_meeting(tmp_path):
    store = SQLiteSourceStore(tmp_path / "source.db")
    SQLiteMeetingRepository(store).save(Meeting(
        meeting_id="m1", title="Payments", transcript="Gateway", meeting_date=datetime.now(timezone.utc), ingested_at=datetime.now(timezone.utc)
    ))
    repository = SQLiteMentionRepository(store)
    for mention_id in ("mention-1", "mention-2"):
        repository.create(EntityMention(
            mention_id=mention_id, entity_type=EntityType.ISSUE, text="gateway", meeting_id="m1",
            source_text="Gateway", created_at=datetime.now(timezone.utc),
        ))
    assert [item.mention_id for item in repository.list_by_meeting_id("m1")] == ["mention-1", "mention-2"]
