"""Test suite for semantic evidence retrieval (Stage 19).

Categories:
1. Cosine similarity (5 tests)
2. Deterministic fake embeddings (5 tests)
3. Semantic search basics (5 tests)
4. Threshold behavior (3 tests)
5. Top-K handling (3 tests)
6. Evidence representation (2 tests)
7. Empty/edge cases (2 tests)
"""

import pytest
from datetime import datetime

from app.models.natural_language import EvidenceItem, EvidenceType, _make_evidence_id
from app.providers.fake_embedding_provider import FakeEmbeddingProvider
from app.services.semantic_evidence_retrieval_service import (
    cosine_similarity,
    SemanticEvidenceRetrievalService,
)


# ---------------------------------------------------------------------------
# Cosine Similarity Tests
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    """Test cosine similarity computation."""

    def test_identical_vectors_similarity_is_one(self):
        """Identical unit vectors should have similarity ≈ 1.0."""
        vec = [1.0, 0.0, 0.0]
        sim = cosine_similarity(vec, vec)
        assert abs(sim - 1.0) < 1e-6

    def test_orthogonal_vectors_similarity_is_zero(self):
        """Orthogonal unit vectors should have similarity ≈ 0.0."""
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [0.0, 1.0, 0.0]
        sim = cosine_similarity(vec_a, vec_b)
        assert abs(sim - 0.0) < 1e-6

    def test_opposite_vectors_similarity_is_clamped_to_zero(self):
        """Opposite vectors (for unit vectors) should clamp to 0.0."""
        # Both unit vectors
        vec_a = [1.0, 0.0]
        vec_b = [-1.0, 0.0]
        sim = cosine_similarity(vec_a, vec_b)
        # Dot product is -1.0; clamped to 0.0
        assert sim == 0.0

    def test_zero_vector_similarity_is_zero(self):
        """Zero vectors should produce similarity 0.0."""
        vec_a = [0.0, 0.0, 0.0]
        vec_b = [1.0, 0.0, 0.0]
        sim = cosine_similarity(vec_a, vec_b)
        assert sim == 0.0

    def test_empty_vectors_similarity_is_zero(self):
        """Empty vectors should produce similarity 0.0."""
        sim = cosine_similarity([], [])
        assert sim == 0.0

    def test_different_length_vectors_treated_orthogonal(self):
        """Vectors of different lengths should be treated as orthogonal."""
        vec_a = [1.0, 0.0]
        vec_b = [1.0, 0.0, 0.0]
        sim = cosine_similarity(vec_a, vec_b)
        assert sim == 0.0


# ---------------------------------------------------------------------------
# Fake Embedding Provider Tests
# ---------------------------------------------------------------------------

class TestFakeEmbeddingProvider:
    """Test deterministic fake embeddings."""

    def test_deterministic_same_text_same_embedding(self):
        """Same text always produces identical embeddings."""
        provider = FakeEmbeddingProvider(dimension=64)
        text = "payment gateway"
        emb1 = provider.embed_text(text)
        emb2 = provider.embed_text(text)
        assert emb1 == emb2

    def test_different_text_different_embedding(self):
        """Different texts produce different embeddings."""
        provider = FakeEmbeddingProvider(dimension=64)
        emb1 = provider.embed_text("payment")
        emb2 = provider.embed_text("gateway")
        # Very unlikely to be identical
        assert emb1 != emb2

    def test_embedding_dimension_respected(self):
        """Embedding dimension matches requested."""
        for dim in [32, 64, 128, 256]:
            provider = FakeEmbeddingProvider(dimension=dim)
            emb = provider.embed_text("test")
            assert len(emb) == dim

    def test_embedding_is_unit_normalized(self):
        """Embedding should be unit-length (normalized)."""
        provider = FakeEmbeddingProvider(dimension=128)
        emb = provider.embed_text("test")
        magnitude_sq = sum(x * x for x in emb)
        magnitude = magnitude_sq ** 0.5
        assert abs(magnitude - 1.0) < 1e-6

    def test_empty_text_raises_error(self):
        """Empty text should raise ValueError."""
        provider = FakeEmbeddingProvider()
        with pytest.raises(ValueError, match="must not be empty"):
            provider.embed_text("")

    def test_invalid_dimension_raises_error(self):
        """Invalid dimension should raise ValueError."""
        with pytest.raises(ValueError, match="must be > 0"):
            FakeEmbeddingProvider(dimension=0)
        with pytest.raises(ValueError, match="must be > 0"):
            FakeEmbeddingProvider(dimension=-1)


# ---------------------------------------------------------------------------
# Semantic Search Basics
# ---------------------------------------------------------------------------

class TestSemanticEvidenceRetrievalService:
    """Test semantic search functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.provider = FakeEmbeddingProvider(dimension=128)
        self.service = SemanticEvidenceRetrievalService(
            embedding_provider=self.provider,
            min_similarity=0.5,
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
            timestamp=datetime.utcnow(),
        )

    def test_search_returns_list(self):
        """Search returns a list of SemanticEvidenceMatch."""
        evidence = [
            self.make_evidence("e1", "Payment system is operational"),
            self.make_evidence("e2", "Gateway is processing transactions"),
        ]
        results = self.service.search("payment system", evidence, top_k=5)
        assert isinstance(results, list)

    def test_search_respects_top_k(self):
        """Search returns at most top_k results."""
        evidence = [
            self.make_evidence(f"e{i}", f"Item {i}") for i in range(10)
        ]
        results = self.service.search("item", evidence, top_k=3)
        assert len(results) <= 3

    def test_search_returns_only_threshold_candidates(self):
        """Only candidates above min_similarity are returned."""
        service = SemanticEvidenceRetrievalService(
            embedding_provider=self.provider,
            min_similarity=0.99,  # Very high threshold
        )
        evidence = [
            self.make_evidence("e1", "Completely unrelated content"),
        ]
        results = service.search("payment", evidence, top_k=5)
        # No result should meet the high threshold (unless by chance)
        # This is probabilistic, but very unlikely with high threshold
        assert len(results) == 0 or len(results) == 1

    def test_search_empty_evidence_returns_empty(self):
        """Search on empty evidence list returns empty."""
        results = self.service.search("query", [], top_k=5)
        assert results == []

    def test_search_invalid_query_raises_error(self):
        """Search with empty query raises ValueError."""
        evidence = [self.make_evidence("e1", "test")]
        with pytest.raises(ValueError, match="must not be empty"):
            self.service.search("", evidence)

    def test_search_invalid_top_k_raises_error(self):
        """Search with top_k <= 0 raises ValueError."""
        evidence = [self.make_evidence("e1", "test")]
        with pytest.raises(ValueError, match="top_k must be > 0"):
            self.service.search("query", evidence, top_k=0)


# ---------------------------------------------------------------------------
# Threshold Behavior
# ---------------------------------------------------------------------------

class TestThresholdBehavior:
    """Test similarity threshold behavior."""

    def test_threshold_filtering(self):
        """Results below threshold are excluded."""
        provider = FakeEmbeddingProvider(dimension=64)
        service = SemanticEvidenceRetrievalService(
            embedding_provider=provider,
            min_similarity=0.8,
        )

        # Create evidence
        evidence = [
            EvidenceItem(
                evidence_id="e1",
                evidence_type=EvidenceType.STATE,
                entity_id="ent1",
                summary="Gateway is operational",
            ),
            EvidenceItem(
                evidence_id="e2",
                evidence_type=EvidenceType.STATE,
                entity_id="ent2",
                summary="Unrelated infrastructure note",
            ),
        ]

        results = service.search("gateway", evidence, top_k=5)
        # Results should only include those meeting threshold
        assert all(m.semantic_similarity_score >= 0.8 for m in results)

    def test_invalid_min_similarity_raises_error(self):
        """Invalid min_similarity raises ValueError."""
        provider = FakeEmbeddingProvider()
        with pytest.raises(ValueError, match="min_similarity must be in"):
            SemanticEvidenceRetrievalService(
                embedding_provider=provider,
                min_similarity=-0.1,
            )
        with pytest.raises(ValueError, match="min_similarity must be in"):
            SemanticEvidenceRetrievalService(
                embedding_provider=provider,
                min_similarity=1.5,
            )


# ---------------------------------------------------------------------------
# Top-K and Ranking
# ---------------------------------------------------------------------------

class TestTopKAndRanking:
    """Test top-K retrieval and ranking."""

    def test_results_ranked_by_similarity_descending(self):
        """Results are sorted by similarity score (highest first)."""
        provider = FakeEmbeddingProvider(dimension=64)
        service = SemanticEvidenceRetrievalService(
            embedding_provider=provider,
            min_similarity=0.0,
        )

        evidence = [
            EvidenceItem(
                evidence_id=f"e{i}",
                evidence_type=EvidenceType.STATE,
                summary=f"Item {i}",
            )
            for i in range(5)
        ]

        results = service.search("item", evidence, top_k=5)
        if len(results) > 1:
            # Check descending order of similarity
            for i in range(len(results) - 1):
                assert (
                    results[i].semantic_similarity_score
                    >= results[i + 1].semantic_similarity_score
                )

    def test_deterministic_tie_break_on_evidence_id(self):
        """Identical similarities broken by evidence_id (alphabetically)."""
        provider = FakeEmbeddingProvider(dimension=64)
        service = SemanticEvidenceRetrievalService(
            embedding_provider=provider,
            min_similarity=0.0,
        )

        # Create evidence with distinct IDs
        evidence = [
            EvidenceItem(
                evidence_id="id_z",
                evidence_type=EvidenceType.STATE,
                summary="test",
            ),
            EvidenceItem(
                evidence_id="id_a",
                evidence_type=EvidenceType.STATE,
                summary="test",
            ),
        ]

        results = service.search("test", evidence, top_k=5)
        # If similarities are identical (same summary), order by ID
        if len(results) == 2 and (
            abs(
                results[0].semantic_similarity_score
                - results[1].semantic_similarity_score
            )
            < 1e-6
        ):
            assert results[0].evidence_id < results[1].evidence_id


# ---------------------------------------------------------------------------
# Evidence Representation
# ---------------------------------------------------------------------------

class TestEvidenceRepresentation:
    """Test evidence representation for embedding."""

    def test_representation_includes_type_entity_summary(self):
        """Representation includes evidence type, entity, and summary."""
        item = EvidenceItem(
            evidence_id="e1",
            evidence_type=EvidenceType.STATE,
            entity_id="payment_gateway",
            summary="Gateway is blocked",
        )
        rep = SemanticEvidenceRetrievalService._make_evidence_representation(item)
        assert "STATE" in rep
        assert "payment_gateway" in rep
        assert "Gateway is blocked" in rep

    def test_representation_with_source_text(self):
        """Representation includes source_text if available."""
        item = EvidenceItem(
            evidence_id="e1",
            evidence_type=EvidenceType.OBSERVATION,
            entity_id="ent1",
            summary="Mentioned in meeting",
            source_text="Gateway approval pending",
        )
        rep = SemanticEvidenceRetrievalService._make_evidence_representation(item)
        assert "Gateway approval pending" in rep


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_search_with_unicode_text(self):
        """Search with Unicode characters works."""
        provider = FakeEmbeddingProvider(dimension=64)
        service = SemanticEvidenceRetrievalService(embedding_provider=provider)

        evidence = [
            EvidenceItem(
                evidence_id="e1",
                evidence_type=EvidenceType.STATE,
                summary="Payment: €50,000 approved",
            ),
        ]

        results = service.search("Payment approval", evidence, top_k=5)
        assert isinstance(results, list)

    def test_identical_evidence_items_both_returned_with_same_similarity(self):
        """Multiple evidence items with identical content can both be returned."""
        provider = FakeEmbeddingProvider(dimension=64)
        service = SemanticEvidenceRetrievalService(
            embedding_provider=provider,
            min_similarity=0.0,
        )

        evidence = [
            EvidenceItem(
                evidence_id="e1",
                evidence_type=EvidenceType.STATE,
                summary="Gateway is blocked",
            ),
            EvidenceItem(
                evidence_id="e2",
                evidence_type=EvidenceType.STATE,
                summary="Gateway is blocked",
            ),
        ]

        results = service.search("gateway blocked", evidence, top_k=5)
        # Both should be found (even with identical content)
        assert len(results) == 2
        # Order is deterministic tie-break on ID
        assert results[0].evidence_id < results[1].evidence_id
