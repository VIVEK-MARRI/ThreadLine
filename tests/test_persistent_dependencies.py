from datetime import datetime, timezone
import sqlite3

import pytest

from app.models.dependency import ExplicitDependency
from app.models.entity import CanonicalEntity, EntityType
from app.models.meeting import Meeting
from app.models.relationships import RelationshipEvidenceType, RelationshipType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.sqlite_source_repositories import SQLiteDependencyRepository, SQLiteEntityRepository, SQLiteMeetingRepository


def setup_store(tmp_path):
    store = SQLiteSourceStore(tmp_path / "source.db")
    SQLiteMeetingRepository(store).save(Meeting(
        meeting_id="m1", title="Payments", transcript="Gateway", meeting_date=datetime.now(timezone.utc), ingested_at=datetime.now(timezone.utc)
    ))
    entities = SQLiteEntityRepository(store)
    for entity_id in ("e1", "e2"):
        entities.create(CanonicalEntity(
            entity_id=entity_id, entity_type=EntityType.ISSUE, canonical_name=entity_id,
            created_at=datetime.now(timezone.utc),
        ))
    return store


def dependency():
    return ExplicitDependency(
        dependency_id="d1", source_entity_id="e1", target_entity_id="e2",
        relationship_type=RelationshipType.BLOCKS,
        evidence_type=RelationshipEvidenceType.EXPLICIT_STATEMENT,
        source_text="e1 blocks e2", meeting_id="m1", mention_id="mention-1",
    )


def test_dependency_survives_restart_and_preserves_id(tmp_path):
    store = setup_store(tmp_path)
    SQLiteDependencyRepository(store).save(dependency())
    restored = SQLiteDependencyRepository(SQLiteSourceStore(tmp_path / "source.db")).get_by_id("d1")
    assert restored.source_entity_id == "e1"
    assert restored.relationship_type == RelationshipType.BLOCKS


def test_foreign_keys_prevent_partial_dependency_write(tmp_path):
    store = setup_store(tmp_path)
    invalid = dependency().model_copy(update={"target_entity_id": "missing"})
    with pytest.raises(sqlite3.IntegrityError):
        SQLiteDependencyRepository(store).save(invalid)
    assert SQLiteDependencyRepository(store).list_all() == []


def test_dependency_upsert_is_idempotent(tmp_path):
    repository = SQLiteDependencyRepository(setup_store(tmp_path))
    repository.save(dependency())
    repository.save(dependency())
    assert len(repository.list_all()) == 1
