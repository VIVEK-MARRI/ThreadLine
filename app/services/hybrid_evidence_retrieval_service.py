"""Hybrid Evidence Retrieval Service (Stage 19).

Merges structured and semantic evidence retrieval with deterministic ranking.

Pipeline
--------
1. Call existing structured retrieval service.
2. Call semantic retrieval service (if applicable).
3. Merge candidates.
4. Deduplicate by evidence_id.
5. Apply ranking policy (structured has priority).
6. Return final candidates.

Ranking Policy
--------------
Priority 1:
    Direct structured evidence for the requested intent/entity.
Priority 2:
    Critical/HIGH attention signals.
Priority 3:
    Explicit state / dependency / impact evidence.
Priority 4:
    Semantic candidates.

Then:
    Recency (timestamp DESC).
    Evidence ID (alphabetically ASC for tie-break).

Safety Boundaries
---------
- Structured evidence remains authoritative.
- Semantic similarity never creates new facts.
- Evidence IDs remain original source identities.
- Ambiguous entities remain ambiguous (no semantic side-channel resolution).
- Unknown intent does not trigger unrestricted semantic search.
- Entity-resolved targets prefer their direct evidence.
- Semantic retrieval only supplements, never overrides.

Design principles
-----------------
- Read-only: never modifies any state.
- Deterministic: same inputs produce same output.
- Conservative: prefers structured signals.
- Transparent: distinguishes structured vs. semantic in metadata.
"""

import logging
from datetime import datetime
from typing import Optional

from app.models.natural_language import (
    EvidenceItem,
    EvidenceType,
    EntityResolutionResult,
    EntityResolutionStatus,
    QueryIntent,
)
from app.services.evidence_retrieval_service import EvidenceRetrievalService
from app.services.semantic_evidence_retrieval_service import (
    SemanticEvidenceRetrievalService,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hybrid Retrieval
# ---------------------------------------------------------------------------

class HybridEvidenceRetrievalService:
    """Merges structured and semantic evidence with safety boundaries."""

    def __init__(
        self,
        structured_service: EvidenceRetrievalService,
        semantic_service: SemanticEvidenceRetrievalService,
        semantic_corpus_provider=None,
        current_revision_lookup=None,
    ) -> None:
        """
        Parameters
        ----------
        structured_service:
            EvidenceRetrievalService (existing).
        semantic_service:
            SemanticEvidenceRetrievalService (new).
        semantic_corpus_provider:
            Callable(current_time) -> list[EvidenceItem] rehydrating the
            authoritative source corpus, or None to skip persisted search.
        current_revision_lookup:
            Optional callable mapping meeting_id to its authoritative current
            source revision; forwarded to semantic persisted search so stale
            or future semantic records are excluded.
        """
        self._structured = structured_service
        self._semantic = semantic_service
        self._semantic_corpus_provider = semantic_corpus_provider
        self._current_revision_lookup = current_revision_lookup

    def retrieve_evidence(
        self,
        intent: QueryIntent,
        entity_id: Optional[str],
        entity_resolution: Optional[EntityResolutionResult],
        query_text: str,
        current_time: datetime,
        max_items: int = 20,
        include_source_text: bool = True,
    ) -> list[EvidenceItem]:
        """Retrieve hybrid evidence: structured + semantic.

        Parameters
        ----------
        intent:
            Query intent.
        entity_id:
            Resolved entity ID if available.
        entity_resolution:
            Resolution status (RESOLVED, AMBIGUOUS, UNRESOLVED).
        query_text:
            Original query text for semantic search.
        current_time:
            Reference datetime.
        max_items:
            Maximum items to retrieve (before context builder bounds).

        Returns
        -------
        list[EvidenceItem]
            Ranked hybrid evidence (structured + semantic).
        """
        if intent == QueryIntent.UNKNOWN:
            # Unknown intent: do not trigger unrestricted semantic search
            # Just return empty (context builder may handle gracefully)
            return []

        # 1. Retrieve structured evidence
        structured_items = self._structured.retrieve_evidence(
            intent=intent,
            entity_id=entity_id,
            current_time=current_time,
            max_items=max_items * 2,  # Room for semantic to supplement
            include_source_text=include_source_text,
        )

        # 2. Determine if we should perform semantic search
        should_semantic_search = self._should_perform_semantic_search(
            intent=intent,
            entity_resolution=entity_resolution,
            has_structured_results=len(structured_items) > 0,
        )

        if not should_semantic_search:
            # No semantic search; return structured only
            return self._rank_evidence(structured_items)

        # 3. Perform semantic search over the persisted corpus, not only the
        # structured result set. The source provider rehydrates authoritative items.
        semantic_candidates = []
        if self._semantic_corpus_provider is not None:
            try:
                semantic_matches = self._semantic.search_persisted(
                    query=query_text,
                    source_lookup={item.evidence_id: item for item in self._semantic_corpus_provider(current_time)}.get,
                    top_k=max_items,
                    current_revision_lookup=self._current_revision_lookup,
                )
                semantic_candidates = [match.evidence for match in semantic_matches]
                if entity_id is not None:
                    structured_ids = {item.evidence_id for item in structured_items}
                    semantic_candidates = [
                        item for item in semantic_candidates
                        if item.entity_id == entity_id or item.evidence_id in structured_ids
                    ]
            except Exception as e:
                # Semantic search failure should not block structured results
                logger.warning(f"Semantic retrieval failed: {e}")
        elif structured_items:
            try:
                semantic_matches = self._semantic.search(query_text, structured_items, max_items)
                semantic_candidates = [match.evidence for match in semantic_matches]
            except Exception as e:
                logger.warning(f"Semantic retrieval failed: {e}")

        # 4. Merge structured + semantic
        merged = self._merge_evidence(structured_items, semantic_candidates)

        # 5. Rank deterministically
        ranked = self._rank_evidence(merged)

        # 6. Return bounded
        return ranked[:max_items]

    @staticmethod
    def _should_perform_semantic_search(
        intent: QueryIntent,
        entity_resolution: Optional[EntityResolutionResult],
        has_structured_results: bool,
    ) -> bool:
        """Determine if semantic retrieval should be attempted.

        Safety: do not perform semantic search for ambiguous entities
        or unknown intent.

        Parameters
        ----------
        intent:
            Query intent.
        entity_resolution:
            Entity resolution status.
        has_structured_results:
            Whether structured retrieval found anything.

        Returns
        -------
        bool
            True if semantic search should proceed.
        """
        # Never for unknown intent
        if intent == QueryIntent.UNKNOWN:
            return False

        # Never for ambiguous entities (would silently resolve)
        if entity_resolution and (
            entity_resolution.status == EntityResolutionStatus.AMBIGUOUS
        ):
            return False

        # Perform for unresolved or resolved entities
        # (helps discover relevant evidence)
        return True

    @staticmethod
    def _merge_evidence(
        structured: list[EvidenceItem],
        semantic: list[EvidenceItem],
    ) -> list[EvidenceItem]:
        """Merge structured and semantic evidence, deduplicating by evidence_id.

        Structured evidence appears first in the result (maintains priority).

        Parameters
        ----------
        structured:
            Structured evidence items.
        semantic:
            Semantic evidence items.

        Returns
        -------
        list[EvidenceItem]
            Merged and deduplicated list.
        """
        seen: dict[str, EvidenceItem] = {}

        # Add structured first
        for item in structured:
            seen[item.evidence_id] = item

        # Add semantic (skip if already in structured)
        for item in semantic:
            if item.evidence_id not in seen:
                seen[item.evidence_id] = item

        return list(seen.values())

    @staticmethod
    def _rank_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
        """Rank evidence deterministically.

        Ranking criteria (in order):
        1. Structured priority weight (inferred from severity_weight and type_priority).
        2. Severity weight DESC (4=CRITICAL, 0=none).
        3. Type priority ASC (1=most relevant, 99=fallback).
        4. Timestamp DESC (most recent first).
        5. Evidence ID ASC (deterministic tie-break).

        Parameters
        ----------
        items:
            List of EvidenceItem (may be unordered).

        Returns
        -------
        list[EvidenceItem]
            Sorted items.
        """

        def sort_key(item: EvidenceItem):
            # Timestamp: convert to sortable numeric (DESC)
            ts = item.timestamp.timestamp() if item.timestamp else -1.0

            return (
                -item.severity_weight,      # DESC
                item.type_priority,         # ASC
                -ts,                        # DESC (negate for sorting)
                item.evidence_id,           # ASC
            )

        return sorted(items, key=sort_key)
