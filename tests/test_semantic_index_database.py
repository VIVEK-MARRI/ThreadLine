"""Stage 21 durable semantic repository tests."""

from datetime import datetime, timezone

from app.models.semantic_index import SemanticIndexRecord
from app.repositories.semantic_index_repository import JsonFileSemanticIndexRepository


def make_record(evidence_id: str, model: str = "fake", version: str = "1.0"):
    return SemanticIndexRecord(
        evidence_id=evidence_id,
        embedding=[0.25, 0.75],
        embedding_model=model,
        embedding_dimension=2,
        representation_hash="a" * 64,
        representation_version=version,
        source_reference="Meeting m1, mention mn1",
        indexed_at=datetime.now(timezone.utc),
    )


def test_json_repository_persists_across_recreation(tmp_path):
    path = tmp_path / "semantic-index.json"
    first = JsonFileSemanticIndexRepository(path)
    first.upsert(make_record("e1"))

    second = JsonFileSemanticIndexRepository(path)
    restored = second.get_by_composite_key("e1", "fake", "1.0")
    assert restored is not None
    assert restored.embedding == [0.25, 0.75]
    assert restored.source_reference == "Meeting m1, mention mn1"


def test_json_repository_upsert_enforces_composite_identity(tmp_path):
    repository = JsonFileSemanticIndexRepository(tmp_path / "index.json")
    repository.upsert(make_record("e1"))
    repository.upsert(make_record("e1"))
    assert repository.count() == 1


def test_json_repository_keeps_models_and_versions_isolated(tmp_path):
    repository = JsonFileSemanticIndexRepository(tmp_path / "index.json")
    repository.upsert(make_record("e1", "model-a", "1.0"))
    repository.upsert(make_record("e1", "model-b", "1.0"))
    repository.upsert(make_record("e1", "model-a", "2.0"))
    assert repository.count() == 3
    assert [record.embedding_model for record in repository.get_by_evidence_id("e1")] == [
        "model-a", "model-a", "model-b"
    ]


def test_json_repository_delete_preserves_source_by_only_deleting_index(tmp_path):
    repository = JsonFileSemanticIndexRepository(tmp_path / "index.json")
    repository.upsert(make_record("e1"))
    assert repository.delete_by_evidence_id("e1") == 1
    assert repository.count() == 0
    assert not (tmp_path / "source-evidence.json").exists()


def test_json_repository_empty_file_is_created_on_first_write(tmp_path):
    path = tmp_path / "nested" / "index.json"
    repository = JsonFileSemanticIndexRepository(path)
    repository.upsert(make_record("e1"))
    assert path.exists()
    assert repository.count() == 1


def test_json_repository_deterministic_listing(tmp_path):
    repository = JsonFileSemanticIndexRepository(tmp_path / "index.json")
    repository.upsert(make_record("e2", "model-b"))
    repository.upsert(make_record("e1", "model-a"))
    assert [(r.embedding_model, r.evidence_id) for r in repository.list_all()] == [
        ("model-a", "e1"), ("model-b", "e2")
    ]
