"""Semantic Evidence Retrieval Service (Stage 19).

Given:
- user query
- existing EvidenceItems

Returns:
- semantic candidate matches ranked by similarity.

The service performs:
1. Query embedding via embedding provider.
2. Cosine similarity matching against evidence embeddings.
3. Threshold filtering.
4. Top-K retrieval with deterministic ranking.

Design principles
-----------------
- Read-only: never modifies any state.
- Similarity is a retrieval score, NOT confidence.
- Original EvidenceItem evidence_id preserved.
- Deterministic ranking with tie-breaks.
- Empty results when threshold not met.
- Safe: no hallucinated evidence.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.models.natural_language import EvidenceItem
from app.providers.embedding_base import AbstractEmbeddingProvider
from app.repositories.semantic_index_repository import AbstractSemanticIndexRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SemanticEvidenceMatch
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticEvidenceMatch:
    """A semantic match result.

    Fields:
    -------
    evidence_id:
        Original EvidenceItem.evidence_id.
    semantic_similarity_score:
        Cosine similarity [0, 1]. Never called "confidence".
    evidence:
        The original EvidenceItem.
    """

    evidence_id: str
    semantic_similarity_score: float
    evidence: EvidenceItem


# ---------------------------------------------------------------------------
# Cosine Similarity
# ---------------------------------------------------------------------------

def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Safe with zero vectors and different-length vectors.

    Parameters
    ----------
    vec_a, vec_b:
        Embedding vectors.

    Returns
    -------
    float
        Similarity in range [0, 1] (both vectors unit-normalized).
        Returns 0.0 for zero vectors or empty vectors.

    Notes
    -----
    Both vectors should be unit-normalized for similarity in [0, 1].
    If vectors are not unit-normalized, result may be negative or >1.
    """
    if not vec_a or not vec_b:
        return 0.0

    if len(vec_a) != len(vec_b):
        # Different dimensions; treat as orthogonal
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))

    # For unit vectors, dot product is in [-1, 1].
    # Clamp to [0, 1] for similarity (assuming unit-normalized vectors).
    similarity = max(0.0, min(1.0, dot_product))

    return similarity


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SemanticEvidenceRetrievalService:
    """Semantic search over existing evidence using embeddings."""

    def __init__(
        self,
        embedding_provider: AbstractEmbeddingProvider,
        min_similarity: float = 0.6,
        repository: Optional[AbstractSemanticIndexRepository] = None,
        embedding_model_name: str = "fake",
        representation_version: str = "1.0",
    ) -> None:
        """
        Parameters
        ----------
        embedding_provider:
            Instance of AbstractEmbeddingProvider (e.g., FakeEmbeddingProvider).
        min_similarity:
            Minimum cosine similarity threshold to include result.
            If no candidate meets threshold, returns [].
            Valid range: [0, 1]. Default: 0.6.
        """
        if not 0.0 <= min_similarity <= 1.0:
            raise ValueError("min_similarity must be in [0, 1]")
        self._embedding_provider = embedding_provider
        self._min_similarity = min_similarity
        self._repository = repository
        self._embedding_model_name = embedding_model_name
        self._representation_version = representation_version

    def search(
        self,
        query: str,
        evidence_items: list[EvidenceItem],
        top_k: int = 5,
    ) -> list[SemanticEvidenceMatch]:
        """Search evidence items semantically.

        Parameters
        ----------
        query:
            User query text. Must not be empty.
        evidence_items:
            List of EvidenceItem to search.
        top_k:
            Maximum number of results to return.
            Must be > 0. Default: 5.

        Returns
        -------
        list[SemanticEvidenceMatch]
            Top-K matches ranked by similarity (highest first).
            Only includes candidates with similarity >= min_similarity.
            Ties broken by evidence_id (alphabetically).
            Empty list if no candidates meet threshold or no evidence provided.

        Raises
        ------
        ValueError
            If query is empty or top_k <= 0.
        EmbeddingError
            If embedding provider fails.
        """
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be > 0")
        if not evidence_items:
            return []

        # 1. Embed the query
        query_embedding = self._embedding_provider.embed_text(query.strip())

        # 2. Use current persisted vectors when configured. Source evidence is
        # still carried by the caller and remains authoritative.
        evidence_embeddings = []
        for item in evidence_items:
            embedding = None
            if self._repository is not None:
                record = self._repository.get_by_composite_key(
                    item.evidence_id,
                    self._embedding_model_name,
                    self._representation_version,
                )
                if record is not None:
                    representation = self._make_evidence_representation(item)
                    from app.services.semantic_indexing_service import SemanticIndexingService

                    expected_hash = SemanticIndexingService._compute_representation_hash(
                        representation
                    )
                    if record.representation_hash == expected_hash:
                        embedding = record.embedding
            if embedding is None:
                if self._repository is not None:
                    # Missing or stale records are excluded rather than
                    # silently re-embedded during a read-only query.
                    continue
                representation = self._make_evidence_representation(item)
                embedding = self._embedding_provider.embed_text(representation)
            evidence_embeddings.append((item, embedding))

        # 3. Compute similarities
        candidates = []
        for item, item_embedding in evidence_embeddings:
            similarity = cosine_similarity(query_embedding, item_embedding)

            # Only include if meets threshold
            if similarity >= self._min_similarity:
                candidates.append(
                    SemanticEvidenceMatch(
                        evidence_id=item.evidence_id,
                        semantic_similarity_score=similarity,
                        evidence=item,
                    )
                )

        # 4. Sort by similarity DESC, then by evidence_id ASC (tie-break)
        ranked = sorted(
            candidates,
            key=lambda x: (-x.semantic_similarity_score, x.evidence_id),
        )

        # 5. Return top-K
        return ranked[:top_k]

    def search_persisted(
        self,
        query: str,
        source_lookup,
        top_k: int = 5,
        current_revision_lookup=None,
    ) -> list[SemanticEvidenceMatch]:
        """Search the full active persisted corpus and rehydrate source items.

        ``source_lookup`` must return the authoritative EvidenceItem for an
        evidence ID, or None when the source item no longer exists. Stale
        records are excluded without indexing or mutating any repository.

        ``current_revision_lookup`` is optional; when provided it maps a
        meeting_id to the meeting's authoritative current source revision.
        Only records whose attribution provably matches the current source
        revision are returned: records with no attribution, or whose stored
        revision differs from the authoritative current revision (stale or
        future), are excluded so a past/future revision never masquerades as
        current.  When omitted, no current-revision filtering is applied.
        """
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be > 0")
        if self._repository is None:
            return []
        query_embedding = self._embedding_provider.embed_text(query.strip())
        from app.services.semantic_indexing_service import SemanticIndexingService

        matches = []
        for record, score in self._repository.search_similar(
            query_embedding, self._embedding_model_name,
            self._representation_version,
            max(top_k, self._repository.count_by_model(self._embedding_model_name)),
            self._min_similarity,
        ):
            evidence = source_lookup(record.evidence_id)
            if evidence is None:
                continue
            expected_hash = SemanticIndexingService._compute_representation_hash(
                self._make_evidence_representation(evidence)
            )
            if expected_hash != record.representation_hash:
                continue
            if current_revision_lookup is not None and not self._is_current_record(
                record, evidence, current_revision_lookup
            ):
                continue
            matches.append(SemanticEvidenceMatch(record.evidence_id, score, evidence))
        return sorted(matches, key=lambda item: (-item.semantic_similarity_score, item.evidence_id))[:top_k]

    @staticmethod
    def _is_current_record(record, evidence, current_revision_lookup) -> bool:
        """Return True only when a semantic record provably targets the
        current authoritative source revision of its owning meeting.

        A record without meeting attribution, or whose stored source_revision
        differs from the authoritative current revision, is NOT current.
        """
        meeting_id = getattr(record, "meeting_id", None)
        if meeting_id is None:
            meeting_id = getattr(evidence, "meeting_id", None)
        record_rev = getattr(record, "source_revision", None)
        if meeting_id is None or record_rev is None:
            return False
        try:
            current_rev = current_revision_lookup(meeting_id)
            return current_rev is not None and int(record_rev) == int(current_rev)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _make_evidence_representation(item: EvidenceItem) -> str:
        """Create a deterministic text representation of an evidence item for embedding.

        Format:
            TYPE | ENTITY | SUMMARY | SOURCE_TEXT

        This representation is DATA ONLY.
        It does NOT include system instructions or provider prompts.

        Parameters
        ----------
        item:
            An EvidenceItem.

        Returns
        -------
        str
            Deterministic representation suitable for embedding.
        """
        parts = [
            item.evidence_type.value,
            item.entity_id or "(no entity)",
            item.summary,
        ]

        # Include source_text if available (but treat as data only)
        if item.source_text:
            parts.append(item.source_text)

        # Join with ' | ' separator
        representation = " | ".join(parts)

        return representation
