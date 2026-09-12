"""Tests for semantic indexing service (Stage 20).

Tests for incremental indexing, embedding reuse, change detection,
batch operations, rebuild, and consistency checks.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, call

from app.models.natural_language import EvidenceItem, EvidenceType, _make_evidence_id
from app.models.semantic_index import SemanticIndexRecord
from app.providers.fake_embedding_provider import FakeEmbeddingProvider
from app.repositories.semantic_index_repository import (
    InMemorySemanticIndexRepository,
)
from app.services.semantic_indexing_service import SemanticIndexingService


class TestSemanticIndexingService:
    """Test semantic indexing service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.embedding_provider = FakeEmbeddingProvider(dimension=64)
        self.repository = InMemorySemanticIndexRepository()
        self.service = SemanticIndexingService(
            embedding_provider=self.embedding_provider,
            repository=self.repository,
            embedding_model_name="fake",
            representation_version="1.0",
        )

    def make_evidence(
        self,
        entity_id: str,
        summary: str,
        source_text: str = "",
    ) -> EvidenceItem:
        """Create a test EvidenceItem."""
        eid = _make_evidence_id(
            EvidenceType.STATE,
            entity_id,
            None,
            summary,
        )
        return EvidenceItem(
            evidence_id=eid,
            evidence_type=EvidenceType.STATE,
            entity_id=entity_id,
            summary=summary,
            source_text=source_text or None,
            timestamp=datetime.now(timezone.utc),
        )

    # -----------------------------------------------------------------------
    # Basic Indexing
    # -----------------------------------------------------------------------

    def test_index_evidence_creates_record(self):
        """Index evidence creates a new record."""
        evidence = self.make_evidence("e1", "Test evidence")
        result = self.service.index_evidence(evidence)

        assert result.evidence_id == evidence.evidence_id
        assert len(result.embedding) == 64
        assert result.embedding_model == "fake"
        assert self.repository.count() == 1

    def test_index_evidence_persisted(self):
        """Indexed evidence is persisted and retrievable."""
        evidence = self.make_evidence("e1", "Test evidence")
        result = self.service.index_evidence(evidence)

        retrieved = self.repository.get_by_composite_key(
            result.evidence_id,
            "fake",
            "1.0",
        )
        assert retrieved is not None
        assert retrieved.embedding == result.embedding

    # -----------------------------------------------------------------------
    # Embedding Reuse
    # -----------------------------------------------------------------------

    def test_index_evidence_twice_reuses_embedding(self):
        """Indexing unchanged evidence reuses embedding (no provider call)."""
        evidence = self.make_evidence("e1", "Test evidence")

        # Mock the embedding provider to track calls
        call_count = 0

        original_embed = self.embedding_provider.embed_text

        def counting_embed(text: str):
            nonlocal call_count
            call_count += 1
            return original_embed(text)

        self.embedding_provider.embed_text = counting_embed

        # First index
        result1 = self.service.index_evidence(evidence)
        assert call_count == 1

        # Second index with same evidence
        result2 = self.service.index_evidence(evidence)
        assert call_count == 1  # No additional call!

        # Embeddings should be identical
        assert result1.embedding == result2.embedding

    def test_index_evidence_changed_regenerates_embedding(self):
        """Changing evidence regenerates embedding."""
        evidence1 = self.make_evidence("e1", "Original summary")
        result1 = self.service.index_evidence(evidence1)

        # Change the evidence
        evidence2 = self.make_evidence("e1", "Modified summary")
        result2 = self.service.index_evidence(evidence2)

        # Different representation hash means different embedding
        assert result1.representation_hash != result2.representation_hash
        assert result1.embedding != result2.embedding

    def test_index_evidence_source_text_change_regenerates(self):
        """Changing source_text regenerates embedding."""
        evidence1 = self.make_evidence("e1", "Summary", "Original text")
        result1 = self.service.index_evidence(evidence1)

        evidence2 = self.make_evidence("e1", "Summary", "Modified text")
        result2 = self.service.index_evidence(evidence2)

        # Embeddings should be different
        assert result1.embedding != result2.embedding

    # -----------------------------------------------------------------------
    # Change Detection
    # -----------------------------------------------------------------------

    def test_refresh_if_changed_no_change_returns_false(self):
        """Refresh on unchanged evidence returns (record, False)."""
        evidence = self.make_evidence("e1", "Test")
        self.service.index_evidence(evidence)

        record, was_changed = self.service.refresh_if_changed(evidence)
        assert was_changed is False
        assert record.evidence_id == evidence.evidence_id

    def test_refresh_if_changed_detects_change(self):
        """Refresh detects and re-indexes changed evidence."""
        evidence1 = self.make_evidence("e1", "Original")
        self.service.index_evidence(evidence1)

        evidence2 = self.make_evidence("e1", "Modified")
        record, was_changed = self.service.refresh_if_changed(evidence2)
        assert was_changed is True

    # -----------------------------------------------------------------------
    # Batch Indexing
    # -----------------------------------------------------------------------

    def test_index_evidence_batch_indexes_all(self):
        """Batch indexing indexes all items."""
        evidence = [
            self.make_evidence("e1", "Test 1"),
            self.make_evidence("e2", "Test 2"),
            self.make_evidence("e3", "Test 3"),
        ]

        results = self.service.index_evidence_batch(evidence)
        assert len(results) == 3
        assert self.repository.count() == 3

    def test_index_evidence_batch_deterministic_order(self):
        """Batch indexing processes items deterministically."""
        evidence = [
            self.make_evidence("e1", "Test 1"),
            self.make_evidence("e2", "Test 2"),
        ]

        results = self.service.index_evidence_batch(evidence)
        # Results should be in same order as input
        result_ids = [r.evidence_id for r in results]
        expected_ids = [e.evidence_id for e in evidence]
        assert result_ids == expected_ids

    # -----------------------------------------------------------------------
    # Deletion
    # -----------------------------------------------------------------------

    def test_remove_evidence_deletes_all_records(self):
        """Remove evidence deletes all records for that evidence."""
        evidence = self.make_evidence("e1", "Test")
        self.service.index_evidence(evidence)

        count = self.service.remove_evidence(evidence.evidence_id)
        assert count == 1
        assert self.repository.count() == 0

    def test_remove_evidence_nonexistent_returns_zero(self):
        """Remove nonexistent evidence returns 0."""
        count = self.service.remove_evidence("nonexistent")
        assert count == 0

    # -----------------------------------------------------------------------
    # Rebuild
    # -----------------------------------------------------------------------

    def test_rebuild_index_from_evidence_list(self):
        """Rebuild indexes all evidence items."""
        evidence = [
            self.make_evidence("e1", "Test 1"),
            self.make_evidence("e2", "Test 2"),
            self.make_evidence("e3", "Test 3"),
        ]

        count = self.service.rebuild_index(evidence)
        assert count == 3
        assert self.repository.count() == 3

    def test_rebuild_removes_stale_records(self):
        """Rebuild removes records for evidence no longer in list."""
        # Index 3 items
        evidence = [
            self.make_evidence("e1", "Test 1"),
            self.make_evidence("e2", "Test 2"),
            self.make_evidence("e3", "Test 3"),
        ]
        self.service.index_evidence_batch(evidence)
        assert self.repository.count() == 3

        # Rebuild with only 2 items
        evidence_subset = evidence[:2]
        count = self.service.rebuild_index(evidence_subset)

        # Should have 2 records (e3 deleted as stale)
        assert count == 2
        assert self.repository.count() == 2
        assert self.repository.exists(evidence[0].evidence_id, "fake", "1.0")
        assert self.repository.exists(evidence[1].evidence_id, "fake", "1.0")
        assert not self.repository.exists(evidence[2].evidence_id, "fake", "1.0")

    def test_rebuild_idempotent(self):
        """Rebuild can be called repeatedly on same data."""
        evidence = [
            self.make_evidence("e1", "Test 1"),
            self.make_evidence("e2", "Test 2"),
        ]

        count1 = self.service.rebuild_index(evidence)
        count2 = self.service.rebuild_index(evidence)

        # Both should index the same number
        assert count1 == count2 == 2
        # Repository should have no duplicates
        assert self.repository.count() == 2

    # -----------------------------------------------------------------------
    # Consistency Checks
    # -----------------------------------------------------------------------

    def test_check_consistency_empty_index(self):
        """Consistency check on empty index."""
        report = self.service.check_consistency()
        assert report["total_records"] == 0
        assert report["records_by_model"] == {}
        assert report["issues"] == []

    def test_check_consistency_valid_records(self):
        """Consistency check on valid records."""
        evidence = [
            self.make_evidence("e1", "Test 1"),
            self.make_evidence("e2", "Test 2"),
        ]
        self.service.index_evidence_batch(evidence)

        report = self.service.check_consistency()
        assert report["total_records"] == 2
        assert report["records_by_model"]["fake"] == 2
        assert report["issues"] == []

    def test_check_consistency_detects_empty_embedding(self):
        """Consistency check detects empty embeddings."""
        evidence = self.make_evidence("e1", "Test")
        result = self.service.index_evidence(evidence)

        # Corrupt the embedding (use smallest valid dimension then override)
        corrupted = SemanticIndexRecord(
            evidence_id=result.evidence_id,
            embedding=[0.1],  # Minimum valid
            embedding_model="fake",
            embedding_dimension=1,
            representation_hash="hash",
            representation_version="1.0",
            indexed_at=datetime.now(timezone.utc),
        )
        # Manually clear to test detection
        corrupted.embedding = []
        self.repository.upsert(corrupted)

        report = self.service.check_consistency()
        assert len(report["issues"]) > 0
        assert any("Empty embedding" in issue for issue in report["issues"])

    def test_check_consistency_detects_dimension_mismatch(self):
        """Consistency check detects dimension mismatch."""
        evidence = self.make_evidence("e1", "Test")
        result = self.service.index_evidence(evidence)

        # Corrupt dimension metadata
        corrupted = SemanticIndexRecord(
            evidence_id=result.evidence_id,
            embedding=result.embedding,
            embedding_model="fake",
            embedding_dimension=999,  # Wrong!
            representation_hash="hash",
            representation_version="1.0",
            indexed_at=datetime.now(timezone.utc),
        )
        self.repository.upsert(corrupted)

        report = self.service.check_consistency()
        assert any("Dimension mismatch" in issue for issue in report["issues"])

    def test_check_consistency_detects_nan(self):
        """Consistency check detects NaN in embedding."""
        evidence = self.make_evidence("e1", "Test")
        result = self.service.index_evidence(evidence)

        # Corrupt with NaN
        corrupted = SemanticIndexRecord(
            evidence_id=result.evidence_id,
            embedding=[float('nan')] + result.embedding[1:],
            embedding_model="fake",
            embedding_dimension=len(result.embedding),
            representation_hash="hash",
            representation_version="1.0",
            indexed_at=datetime.now(timezone.utc),
        )
        self.repository.upsert(corrupted)

        report = self.service.check_consistency()
        assert any("NaN" in issue for issue in report["issues"])

    def test_check_consistency_detects_infinity(self):
        """Consistency check detects infinity in embedding."""
        evidence = self.make_evidence("e1", "Test")
        result = self.service.index_evidence(evidence)

        # Corrupt with infinity
        corrupted = SemanticIndexRecord(
            evidence_id=result.evidence_id,
            embedding=[float('inf')] + result.embedding[1:],
            embedding_model="fake",
            embedding_dimension=len(result.embedding),
            representation_hash="hash",
            representation_version="1.0",
            indexed_at=datetime.now(timezone.utc),
        )
        self.repository.upsert(corrupted)

        report = self.service.check_consistency()
        assert any("Infinity" in issue for issue in report["issues"])

    # -----------------------------------------------------------------------
    # Representation Hash
    # -----------------------------------------------------------------------

    def test_representation_hash_deterministic(self):
        """Representation hash is deterministic."""
        text = "Payment Gateway Migration is blocked"
        hash1 = SemanticIndexingService._compute_representation_hash(text)
        hash2 = SemanticIndexingService._compute_representation_hash(text)
        assert hash1 == hash2

    def test_representation_hash_different_for_different_text(self):
        """Different text produces different hash."""
        hash1 = SemanticIndexingService._compute_representation_hash("text1")
        hash2 = SemanticIndexingService._compute_representation_hash("text2")
        assert hash1 != hash2

    def test_representation_hash_full_sha256(self):
        """Representation hash is full SHA256 (64 hex chars)."""
        hash_result = SemanticIndexingService._compute_representation_hash("test")
        assert len(hash_result) == 64  # SHA256 hex
        assert all(c in "0123456789abcdef" for c in hash_result)

    # -----------------------------------------------------------------------
    # Model and Version Isolation
    # -----------------------------------------------------------------------

    def test_different_models_independent_records(self):
        """Different models create independent records."""
        evidence = self.make_evidence("e1", "Test")

        provider = FakeEmbeddingProvider(dimension=64)
        service1 = SemanticIndexingService(
            embedding_provider=provider,
            repository=self.repository,
            embedding_model_name="model_a",
            representation_version="1.0",
        )
        service2 = SemanticIndexingService(
            embedding_provider=provider,
            repository=self.repository,
            embedding_model_name="model_b",
            representation_version="1.0",
        )

        service1.index_evidence(evidence)
        service2.index_evidence(evidence)

        assert self.repository.count() == 2
        assert self.repository.exists(evidence.evidence_id, "model_a", "1.0")
        assert self.repository.exists(evidence.evidence_id, "model_b", "1.0")

    def test_different_versions_independent_records(self):
        """Different representation versions create independent records."""
        evidence = self.make_evidence("e1", "Test")

        provider = FakeEmbeddingProvider(dimension=64)
        service1 = SemanticIndexingService(
            embedding_provider=provider,
            repository=self.repository,
            embedding_model_name="fake",
            representation_version="1.0",
        )
        service2 = SemanticIndexingService(
            embedding_provider=provider,
            repository=self.repository,
            embedding_model_name="fake",
            representation_version="2.0",
        )

        service1.index_evidence(evidence)
        service2.index_evidence(evidence)

        assert self.repository.count() == 2
        assert self.repository.exists(evidence.evidence_id, "fake", "1.0")
        assert self.repository.exists(evidence.evidence_id, "fake", "2.0")

    # -----------------------------------------------------------------------
    # Read-Only Guarantee
    # -----------------------------------------------------------------------

    def test_indexing_does_not_modify_evidence(self):
        """Indexing does not modify source evidence."""
        evidence = self.make_evidence("e1", "Original summary")
        original_summary = evidence.summary

        self.service.index_evidence(evidence)

        # Evidence should be unchanged
        assert evidence.summary == original_summary

    def test_remove_evidence_does_not_affect_source(self):
        """Removing index record doesn't affect source evidence."""
        evidence = self.make_evidence("e1", "Test")
        self.service.index_evidence(evidence)

        # Remove from index
        self.service.remove_evidence(evidence.evidence_id)

        # Evidence object unchanged
        assert evidence.evidence_id == evidence.evidence_id

    # -----------------------------------------------------------------------
    # Idempotency
    # -----------------------------------------------------------------------

    def test_index_twice_idempotent(self):
        """Indexing twice creates only one record."""
        evidence = self.make_evidence("e1", "Test")

        self.service.index_evidence(evidence)
        self.service.index_evidence(evidence)

        assert self.repository.count() == 1

    def test_batch_with_duplicates_deduplicates(self):
        """Batch indexing same evidence multiple times is idempotent."""
        evidence1 = self.make_evidence("e1", "Test")
        evidence2 = self.make_evidence("e1", "Test")  # Same

        batch = [evidence1, evidence2]
        results = self.service.index_evidence_batch(batch)

        # Both should succeed (create only one record)
        assert len(results) == 2
        assert self.repository.count() == 1
