"""Tests for semantic index persistence (Stage 20).

Tests for repository abstraction and in-memory implementation.
Covers CRUD operations, composite keys, and deterministic ordering.
"""

import pytest
from datetime import datetime, timezone

from app.models.semantic_index import SemanticIndexRecord
from app.repositories.semantic_index_repository import (
    InMemorySemanticIndexRepository,
)


class TestInMemorySemanticIndexRepository:
    """Test in-memory semantic index repository."""

    def setup_method(self):
        """Set up test fixtures."""
        self.repo = InMemorySemanticIndexRepository()

    def make_record(
        self,
        evidence_id: str,
        model: str = "fake",
        version: str = "1.0",
        embedding_dim: int = 64,
    ) -> SemanticIndexRecord:
        """Create a test record."""
        embedding = [0.1 * i for i in range(embedding_dim)]
        return SemanticIndexRecord(
            evidence_id=evidence_id,
            embedding=embedding,
            embedding_model=model,
            embedding_dimension=embedding_dim,
            representation_hash=f"hash_{evidence_id}",
            representation_version=version,
            source_reference=f"meeting for {evidence_id}",
            indexed_at=datetime.now(timezone.utc),
        )

    # -----------------------------------------------------------------------
    # Basic CRUD
    # -----------------------------------------------------------------------

    def test_upsert_creates_new_record(self):
        """Upsert inserts a new record."""
        record = self.make_record("e1")
        result = self.repo.upsert(record)
        assert result.evidence_id == "e1"
        assert self.repo.count() == 1

    def test_upsert_updates_existing_record(self):
        """Upsert updates existing record by composite key."""
        record1 = self.make_record("e1", version="1.0")
        self.repo.upsert(record1)

        # Upsert same composite key with different embedding
        record2 = self.make_record("e1", version="1.0")
        record2.representation_hash = "new_hash"
        self.repo.upsert(record2)

        # Only one record should exist
        assert self.repo.count() == 1
        retrieved = self.repo.get_by_composite_key("e1", "fake", "1.0")
        assert retrieved.representation_hash == "new_hash"

    def test_get_by_composite_key_found(self):
        """Get by composite key returns record if found."""
        record = self.make_record("e1", model="openai", version="2.0")
        self.repo.upsert(record)

        retrieved = self.repo.get_by_composite_key("e1", "openai", "2.0")
        assert retrieved is not None
        assert retrieved.evidence_id == "e1"

    def test_get_by_composite_key_not_found(self):
        """Get by composite key returns None if not found."""
        retrieved = self.repo.get_by_composite_key("e1", "fake", "1.0")
        assert retrieved is None

    def test_delete_by_composite_key_success(self):
        """Delete by composite key removes record."""
        record = self.make_record("e1")
        self.repo.upsert(record)

        result = self.repo.delete("e1", "fake", "1.0")
        assert result is True
        assert self.repo.count() == 0

    def test_delete_by_composite_key_not_found(self):
        """Delete by composite key returns False if not found."""
        result = self.repo.delete("e1", "fake", "1.0")
        assert result is False

    def test_delete_by_evidence_id_removes_all(self):
        """Delete by evidence_id removes all records for that evidence."""
        self.repo.upsert(self.make_record("e1", version="1.0"))
        self.repo.upsert(self.make_record("e1", version="2.0"))
        self.repo.upsert(self.make_record("e1", model="openai", version="1.0"))
        self.repo.upsert(self.make_record("e2", version="1.0"))

        count = self.repo.delete_by_evidence_id("e1")
        assert count == 3
        assert self.repo.count() == 1

    def test_exists_returns_true_when_present(self):
        """Exists returns True when record exists."""
        record = self.make_record("e1")
        self.repo.upsert(record)

        result = self.repo.exists("e1", "fake", "1.0")
        assert result is True

    def test_exists_returns_false_when_absent(self):
        """Exists returns False when record absent."""
        result = self.repo.exists("e1", "fake", "1.0")
        assert result is False

    # -----------------------------------------------------------------------
    # Query Operations
    # -----------------------------------------------------------------------

    def test_get_by_evidence_id_returns_all_models_and_versions(self):
        """Get by evidence_id returns all records for that evidence."""
        self.repo.upsert(self.make_record("e1", model="fake", version="1.0"))
        self.repo.upsert(self.make_record("e1", model="fake", version="2.0"))
        self.repo.upsert(self.make_record("e1", model="openai", version="1.0"))
        self.repo.upsert(self.make_record("e2", model="fake", version="1.0"))

        results = self.repo.get_by_evidence_id("e1")
        assert len(results) == 3
        assert all(r.evidence_id == "e1" for r in results)

    def test_get_by_evidence_id_empty(self):
        """Get by evidence_id returns empty list if not found."""
        results = self.repo.get_by_evidence_id("e1")
        assert results == []

    def test_get_by_evidence_id_deterministic_order(self):
        """Get by evidence_id returns results in deterministic order."""
        # Insert in random order
        self.repo.upsert(self.make_record("e1", model="z", version="2"))
        self.repo.upsert(self.make_record("e1", model="a", version="2"))
        self.repo.upsert(self.make_record("e1", model="a", version="1"))

        results = self.repo.get_by_evidence_id("e1")
        models_versions = [(r.embedding_model, r.representation_version) for r in results]

        # Should be sorted by (model, version)
        assert models_versions == [("a", "1"), ("a", "2"), ("z", "2")]

    def test_list_by_model_returns_all_for_model(self):
        """List by model returns all records for that model."""
        self.repo.upsert(self.make_record("e1", model="fake"))
        self.repo.upsert(self.make_record("e2", model="fake"))
        self.repo.upsert(self.make_record("e3", model="openai"))

        results = self.repo.list_by_model("fake")
        assert len(results) == 2
        assert all(r.embedding_model == "fake" for r in results)

    def test_list_by_model_deterministic_order(self):
        """List by model returns results in deterministic order."""
        self.repo.upsert(self.make_record("e3", model="fake", version="1"))
        self.repo.upsert(self.make_record("e1", model="fake", version="1"))
        self.repo.upsert(self.make_record("e2", model="fake", version="1"))

        results = self.repo.list_by_model("fake")
        evidence_ids = [r.evidence_id for r in results]
        assert evidence_ids == ["e1", "e2", "e3"]

    def test_list_all_returns_all_records(self):
        """List all returns all records."""
        self.repo.upsert(self.make_record("e1"))
        self.repo.upsert(self.make_record("e2"))
        self.repo.upsert(self.make_record("e3"))

        results = self.repo.list_all()
        assert len(results) == 3

    def test_list_all_deterministic_order(self):
        """List all returns records in deterministic order."""
        # Insert in random order
        self.repo.upsert(self.make_record("e2", model="z", version="2"))
        self.repo.upsert(self.make_record("e1", model="a", version="1"))
        self.repo.upsert(self.make_record("e3", model="a", version="1"))

        results = self.repo.list_all()
        keys = [(r.embedding_model, r.evidence_id, r.representation_version) for r in results]

        # Should be sorted by (model, evidence_id, version)
        assert keys == [
            ("a", "e1", "1"),
            ("a", "e3", "1"),
            ("z", "e2", "2"),
        ]

    # -----------------------------------------------------------------------
    # Counting
    # -----------------------------------------------------------------------

    def test_count_returns_total(self):
        """Count returns total record count."""
        self.repo.upsert(self.make_record("e1"))
        self.repo.upsert(self.make_record("e2"))
        assert self.repo.count() == 2

    def test_count_by_model(self):
        """Count by model returns count for specific model."""
        self.repo.upsert(self.make_record("e1", model="fake"))
        self.repo.upsert(self.make_record("e2", model="fake"))
        self.repo.upsert(self.make_record("e3", model="openai"))

        count_fake = self.repo.count_by_model("fake")
        count_openai = self.repo.count_by_model("openai")
        assert count_fake == 2
        assert count_openai == 1

    # -----------------------------------------------------------------------
    # Composite Key Safety
    # -----------------------------------------------------------------------

    def test_composite_key_safety_evidence_id_matters(self):
        """Different evidence_ids are separate records."""
        self.repo.upsert(self.make_record("e1", model="fake"))
        self.repo.upsert(self.make_record("e2", model="fake"))
        assert self.repo.count() == 2

    def test_composite_key_safety_model_matters(self):
        """Different models are separate records."""
        self.repo.upsert(self.make_record("e1", model="fake"))
        self.repo.upsert(self.make_record("e1", model="openai"))
        assert self.repo.count() == 2

    def test_composite_key_safety_version_matters(self):
        """Different versions are separate records."""
        self.repo.upsert(self.make_record("e1", version="1.0"))
        self.repo.upsert(self.make_record("e1", version="2.0"))
        assert self.repo.count() == 2

    # -----------------------------------------------------------------------
    # Edge Cases
    # -----------------------------------------------------------------------

    def test_empty_repository(self):
        """Empty repository behaves correctly."""
        assert self.repo.count() == 0
        assert self.repo.list_all() == []
        assert self.repo.list_by_model("any") == []

    def test_embedding_preservation(self):
        """Embeddings are preserved exactly."""
        embedding = [0.123456789, -0.987654321, 0.0, 1.0]
        record = self.make_record("e1")
        record.embedding = embedding
        self.repo.upsert(record)

        retrieved = self.repo.get_by_composite_key("e1", "fake", "1.0")
        assert retrieved.embedding == embedding

    def test_zero_length_embedding(self):
        """Zero-length embedding is accepted (though invalid semantically)."""
        record = self.make_record("e1")
        record.embedding = []
        record.embedding_dimension = 0
        self.repo.upsert(record)

        retrieved = self.repo.get_by_composite_key("e1", "fake", "1.0")
        assert retrieved.embedding == []
        assert retrieved.embedding_dimension == 0
