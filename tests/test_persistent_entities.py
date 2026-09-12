from datetime import datetime, timezone

from app.models.entity import CanonicalEntity, EntityType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.sqlite_source_repositories import SQLiteEntityRepository


def entity(entity_id="e1", name="Payments Gateway"):
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=EntityType.ISSUE,
        canonical_name=name,
        aliases=["gateway"],
        created_at=datetime.now(timezone.utc),
    )


def test_entity_and_alias_survive_recreation(tmp_path):
    path = tmp_path / "source.db"
    store = SQLiteSourceStore(path)
    repository = SQLiteEntityRepository(store)
    repository.create(entity())
    repository.add_alias("e1", "payments")
    store.close()
    restored = SQLiteEntityRepository(SQLiteSourceStore(path)).get_by_id("e1")
    assert restored.aliases == ["gateway", "payments"]


def test_entity_lookup_preserves_exact_resolution_semantics(tmp_path):
    repository = SQLiteEntityRepository(SQLiteSourceStore(tmp_path / "source.db"))
    repository.create(entity())
    assert repository.find_by_canonical_name("  PAYMENTS   GATEWAY ", EntityType.ISSUE).entity_id == "e1"
    assert repository.find_by_canonical_name("gateway", EntityType.ISSUE).entity_id == "e1"
