from datetime import datetime, timezone

from app.models.dependency import ExplicitDependency
from app.models.entity import CanonicalEntity, EntityType
from app.models.meeting import Meeting
from app.models.relationships import RelationshipEvidenceType, RelationshipType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.sqlite_source_repositories import SQLiteDependencyRepository, SQLiteEntityRepository, SQLiteMeetingRepository, SQLiteMentionRepository
from app.services.entity_relationship_service import EntityRelationshipService


def test_relationship_graph_rebuilds_from_durable_explicit_dependency(tmp_path):
    store = SQLiteSourceStore(tmp_path / "source.db")
    SQLiteMeetingRepository(store).save(Meeting(
        meeting_id="m1", title="Payments", transcript="Gateway", meeting_date=datetime.now(timezone.utc), ingested_at=datetime.now(timezone.utc)
    ))
    entities = SQLiteEntityRepository(store)
    for entity_id in ("e1", "e2"):
        entities.create(CanonicalEntity(entity_id=entity_id, entity_type=EntityType.ISSUE, canonical_name=entity_id, created_at=datetime.now(timezone.utc)))
    SQLiteDependencyRepository(store).save(ExplicitDependency(
        dependency_id="d1", source_entity_id="e1", target_entity_id="e2",
        relationship_type=RelationshipType.DEPENDS_ON,
        evidence_type=RelationshipEvidenceType.EXPLICIT_STATEMENT,
        source_text="e1 depends on e2", meeting_id="m1", mention_id="mention-1",
    ))
    graph = EntityRelationshipService(entities, SQLiteMentionRepository(store), SQLiteDependencyRepository(store)).get_relationship_graph("e1")
    assert any(item.relationship_type == RelationshipType.DEPENDS_ON for item in graph.relationships)
