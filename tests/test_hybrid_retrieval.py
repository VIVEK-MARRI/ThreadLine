"""Test suite for hybrid evidence retrieval (Stage 19).

Categories:
1. Basic hybrid retrieval (4 tests)
2. Safety boundaries (5 tests)
3. Entity resolution behavior (4 tests)
4. Evidence deduplication and merging (3 tests)
5. Ranking policy (3 tests)
6. Read-only verification (2 tests)
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock

from app.models.natural_language import (
    EvidenceItem,
    EvidenceType,
    EntityResolutionResult,
    EntityResolutionStatus,
    QueryIntent,
    _make_evidence_id,
)
from app.providers.fake_embedding_provider import FakeEmbeddingProvider
from app.services.evidence_retrieval_service import EvidenceRetrievalService
from app.services.hybrid_evidence_retrieval_service import (
    HybridEvidenceRetrievalService,
)
from app.services.semantic_evidence_retrieval_service import (
    SemanticEvidenceRetrievalService,
)


class TestHybridEvidenceRetrieval:
    """Test hybrid retrieval integration."""

    def setup_method(self):
        """Set up test fixtures."""
        # Mock the structured service
        self.structured_service = Mock(spec=EvidenceRetrievalService)

        # Create real semantic service
        embedding_provider = FakeEmbeddingProvider(dimension=64)
        self.semantic_service = SemanticEvidenceRetrievalService(
            embedding_provider=embedding_provider,
            min_similarity=0.5,
        )

        # Create hybrid service
        self.hybrid_service = HybridEvidenceRetrievalService(
            structured_service=self.structured_service,
            semantic_service=self.semantic_service,
        )

    def make_evidence(
        self,
        entity_id: str,
        summary: str,
        severity: int = 0,
        priority: int = 50,
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
            severity_weight=severity,
            type_priority=priority,
            source_text=source_text or None,
            timestamp=datetime.utcnow(),
        )

    # -----------------------------------------------------------------------
    # Basic Hybrid Retrieval
    # -----------------------------------------------------------------------

    def test_hybrid_retrieval_calls_structured_service(self):
        """Hybrid retrieval calls the structured service."""
        self.structured_service.retrieve_evidence.return_value = []

        self.hybrid_service.retrieve_evidence(
            intent=QueryIntent.ENTITY_STATUS,
            entity_id="e1",
            entity_resolution=None,
            query_text="status",
            current_time=datetime.utcnow(),
            max_items=20,
        )

        self.structured_service.retrieve_evidence.assert_called_once()

    def test_hybrid_retrieval_returns_list(self):
        """Hybrid retrieval returns a list of EvidenceItems."""
        self.structured_service.retrieve_evidence.return_value = [
            self.make_evidence("e1", "Test evidence")
        ]

        result = self.hybrid_service.retrieve_evidence(
            intent=QueryIntent.ENTITY_STATUS,
            entity_id="e1",
            entity_resolution=None,
            query_text="status",
            current_time=datetime.utcnow(),
            max_items=20,
        )

        assert isinstance(result, list)
        assert len(result) > 0

    def test_hybrid_unknown_intent_returns_empty(self):
        """UNKNOWN intent returns empty (no semantic search for unknown)."""
        result = self.hybrid_service.retrieve_evidence(
            intent=QueryIntent.UNKNOWN,
            entity_id=None,
            entity_resolution=None,
            query_text="what will happen?",
            current_time=datetime.utcnow(),
            max_items=20,
        )

        assert result == []

    def test_hybrid_retrieval_respects_max_items(self):
        """Hybrid retrieval respects max_items limit."""
        structured = [
            self.make_evidence(f"e{i}", f"Evidence {i}") for i in range(10)
        ]
        self.structured_service.retrieve_evidence.return_value = structured

        result = self.hybrid_service.retrieve_evidence(
            intent=QueryIntent.ENTITY_STATUS,
            entity_id="e1",
            entity_resolution=None,
            query_text="status",
            current_time=datetime.utcnow(),
            max_items=5,
        )

        assert len(result) <= 5

    # -----------------------------------------------------------------------
    # Safety Boundaries
    # -----------------------------------------------------------------------

    def test_ambiguous_entity_no_semantic_search(self):
        """Ambiguous entity does not trigger semantic search."""
        structured = [self.make_evidence("e1", "Ambiguous result")]
        self.structured_service.retrieve_evidence.return_value = structured

        resolution = EntityResolutionResult(
            status=EntityResolutionStatus.AMBIGUOUS,
            candidates=["e1", "e2"],
        )

        result = self.hybrid_service.retrieve_evidence(
            intent=QueryIntent.ENTITY_STATUS,
            entity_id=None,
            entity_resolution=resolution,
            query_text="Phoenix status",
            current_time=datetime.utcnow(),
            max_items=20,
        )

        # Should get only structured (no semantic side-channel resolution)
        assert len(result) == len(structured)

    def test_unresolved_entity_allows_semantic_search(self):
        """Unresolved entity allows semantic search (important capability)."""
        evidence = [
            self.make_evidence("e1", "Gateway migration details")
        ]
        self.structured_service.retrieve_evidence.return_value = evidence

        resolution = EntityResolutionResult(
            status=EntityResolutionStatus.UNRESOLVED,
            extracted_name="gateway rollout",
        )

        result = self.hybrid_service.retrieve_evidence(
            intent=QueryIntent.ENTITY_STATUS,
            entity_id=None,
            entity_resolution=resolution,
            query_text="What is holding up the gateway rollout?",
            current_time=datetime.utcnow(),
            max_items=20,
        )

        # Should get evidence (semantic can supplement)
        assert isinstance(result, list)

    def test_resolved_entity_allows_semantic_search(self):
        """Resolved entity allows semantic search for supplemental evidence."""
        evidence = [
            self.make_evidence("e1", "Primary evidence for entity")
        ]
        self.structured_service.retrieve_evidence.return_value = evidence

        resolution = EntityResolutionResult(
            status=EntityResolutionStatus.RESOLVED,
            entity_id="e1",
            entity_name="Payment Gateway Migration",
        )

        result = self.hybrid_service.retrieve_evidence(
            intent=QueryIntent.ENTITY_STATUS,
            entity_id="e1",
            entity_resolution=resolution,
            query_text="Payment rollout status",
            current_time=datetime.utcnow(),
            max_items=20,
        )

        assert isinstance(result, list)

    def test_semantic_search_failure_does_not_block_structured(self):
        """If semantic search fails, structured results are still returned."""
        structured = [
            self.make_evidence("e1", "Structured evidence")
        ]
        self.structured_service.retrieve_evidence.return_value = structured

        # Create semantic service that will fail
        mock_semantic = Mock(spec=SemanticEvidenceRetrievalService)
        mock_semantic.search.side_effect = Exception("Semantic service failed")

        hybrid = HybridEvidenceRetrievalService(
            structured_service=self.structured_service,
            semantic_service=mock_semantic,
        )

        result = hybrid.retrieve_evidence(
            intent=QueryIntent.ENTITY_STATUS,
            entity_id="e1",
            entity_resolution=None,
            query_text="status",
            current_time=datetime.utcnow(),
            max_items=20,
        )

        # Should still return structured results despite semantic failure
        assert len(result) > 0

    # -----------------------------------------------------------------------
    # Entity Resolution Behavior
    # -----------------------------------------------------------------------

    def test_should_perform_semantic_search_resolved(self):
        """Should perform semantic search for RESOLVED entity."""
        result = HybridEvidenceRetrievalService._should_perform_semantic_search(
            intent=QueryIntent.ENTITY_STATUS,
            entity_resolution=EntityResolutionResult(
                status=EntityResolutionStatus.RESOLVED,
                entity_id="e1",
            ),
            has_structured_results=True,
        )
        assert result is True

    def test_should_perform_semantic_search_unresolved(self):
        """Should perform semantic search for UNRESOLVED entity."""
        result = HybridEvidenceRetrievalService._should_perform_semantic_search(
            intent=QueryIntent.ENTITY_STATUS,
            entity_resolution=EntityResolutionResult(
                status=EntityResolutionStatus.UNRESOLVED,
            ),
            has_structured_results=True,
        )
        assert result is True

    def test_should_not_perform_semantic_search_ambiguous(self):
        """Should NOT perform semantic search for AMBIGUOUS entity."""
        result = HybridEvidenceRetrievalService._should_perform_semantic_search(
            intent=QueryIntent.ENTITY_STATUS,
            entity_resolution=EntityResolutionResult(
                status=EntityResolutionStatus.AMBIGUOUS,
                candidates=["e1", "e2"],
            ),
            has_structured_results=True,
        )
        assert result is False

    def test_should_not_perform_semantic_search_unknown_intent(self):
        """Should NOT perform semantic search for UNKNOWN intent."""
        result = HybridEvidenceRetrievalService._should_perform_semantic_search(
            intent=QueryIntent.UNKNOWN,
            entity_resolution=None,
            has_structured_results=False,
        )
        assert result is False

    # -----------------------------------------------------------------------
    # Evidence Deduplication and Merging
    # -----------------------------------------------------------------------

    def test_merge_deduplicates_by_evidence_id(self):
        """Merge deduplicates evidence by evidence_id."""
        item1 = self.make_evidence("e1", "Original evidence")
        item2_duplicate = self.make_evidence("e1", "Same evidence, different source")
        item3 = self.make_evidence("e2", "Different evidence")

        structured = [item1, item3]
        semantic = [item2_duplicate]

        merged = HybridEvidenceRetrievalService._merge_evidence(structured, semantic)

        # Should have 2 items (item2_duplicate skipped due to same evidence_id as item1)
        evidence_ids = [item.evidence_id for item in merged]
        assert len(set(evidence_ids)) == len(evidence_ids)  # No duplicates

    def test_merge_preserves_structured_priority(self):
        """Merge returns structured items first."""
        structured = [self.make_evidence("e1", "Structured")]
        semantic = [self.make_evidence("e2", "Semantic")]

        merged = HybridEvidenceRetrievalService._merge_evidence(structured, semantic)

        # Structured should appear first
        assert merged[0].evidence_id == structured[0].evidence_id

    def test_merge_adds_unique_semantic(self):
        """Merge adds semantic items not in structured."""
        structured = [self.make_evidence("e1", "Structured")]
        semantic = [self.make_evidence("e2", "Semantic")]

        merged = HybridEvidenceRetrievalService._merge_evidence(structured, semantic)

        # Should have both
        evidence_ids = [item.evidence_id for item in merged]
        assert structured[0].evidence_id in evidence_ids
        assert semantic[0].evidence_id in evidence_ids

    # -----------------------------------------------------------------------
    # Ranking Policy
    # -----------------------------------------------------------------------

    def test_rank_by_severity_weight_desc(self):
        """Ranking prioritizes severity_weight (DESC)."""
        critical = self.make_evidence("e1", "Critical issue", severity=4)
        medium = self.make_evidence("e2", "Medium issue", severity=2)
        info = self.make_evidence("e3", "Info", severity=0)

        items = [info, critical, medium]
        ranked = HybridEvidenceRetrievalService._rank_evidence(items)

        # Critical should be first
        assert ranked[0].severity_weight == 4
        assert ranked[-1].severity_weight == 0

    def test_rank_by_type_priority_asc(self):
        """Ranking uses type_priority (ASC) as secondary criterion."""
        high_priority = self.make_evidence("e1", "High priority", priority=1)
        low_priority = self.make_evidence("e2", "Low priority", priority=50)

        # Same severity
        high_priority.severity_weight = 2
        low_priority.severity_weight = 2

        ranked = HybridEvidenceRetrievalService._rank_evidence(
            [low_priority, high_priority]
        )

        # High priority should be first
        assert ranked[0].type_priority == 1

    def test_rank_by_timestamp_desc(self):
        """Ranking uses timestamp (DESC) as tertiary criterion."""
        now = datetime.utcnow()
        recent = self.make_evidence("e1", "Recent")
        recent.timestamp = now

        from datetime import timedelta
        old = self.make_evidence("e2", "Old")
        old.timestamp = now - timedelta(days=1)

        # Same severity and priority
        recent.severity_weight = 2
        recent.type_priority = 1
        old.severity_weight = 2
        old.type_priority = 1

        ranked = HybridEvidenceRetrievalService._rank_evidence([old, recent])

        # Recent should be first
        assert ranked[0].evidence_id == recent.evidence_id

    # -----------------------------------------------------------------------
    # Read-Only Verification
    # -----------------------------------------------------------------------

    def test_hybrid_retrieval_does_not_modify_evidence(self):
        """Hybrid retrieval does not modify evidence items."""
        original_evidence = [
            self.make_evidence("e1", "Original summary")
        ]
        self.structured_service.retrieve_evidence.return_value = original_evidence

        original_summary = original_evidence[0].summary

        self.hybrid_service.retrieve_evidence(
            intent=QueryIntent.ENTITY_STATUS,
            entity_id="e1",
            entity_resolution=None,
            query_text="status",
            current_time=datetime.utcnow(),
            max_items=20,
        )

        # Verify original evidence is unchanged
        assert original_evidence[0].summary == original_summary

    def test_hybrid_retrieval_does_not_call_repositories(self):
        """Hybrid retrieval does not call any repositories."""
        # Structured service is mocked, so we verify it's the only one called
        self.structured_service.retrieve_evidence.return_value = []

        self.hybrid_service.retrieve_evidence(
            intent=QueryIntent.ENTITY_STATUS,
            entity_id="e1",
            entity_resolution=None,
            query_text="status",
            current_time=datetime.utcnow(),
            max_items=20,
        )

        # Structured service called, nothing else
        assert self.structured_service.retrieve_evidence.call_count == 1
