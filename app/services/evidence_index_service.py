"""Evidence Index Service (Stage 19).

In-memory indexing and embedding caching for evidence items.

The index stores:
- Original EvidenceItem objects
- Pre-computed embeddings
- Metadata for tracking changes

Design principles
-----------------
- In-memory only: no persistent vector database.
- Deterministic: rebuilt from current evidence on demand or explicitly.
- Read-only: indexing does not modify source evidence.
- Efficient: caches embeddings to avoid recomputation within a query.

Note on cache stability
---------
Cache key includes:
- evidence_id
- embedding provider/model
- evidence representation

If evidence content changes, embedding should be recomputed.
For now, index is built fresh each query session.
"""

from dataclasses import dataclass, field
from typing import Optional

from app.models.natural_language import EvidenceItem
from app.providers.embedding_base import AbstractEmbeddingProvider
from app.services.semantic_evidence_retrieval_service import (
    SemanticEvidenceRetrievalService,
)


# ---------------------------------------------------------------------------
# IndexedEvidence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IndexedEvidence:
    """An evidence item with its pre-computed embedding.

    Fields:
    -------
    evidence:
        The original EvidenceItem.
    embedding:
        Pre-computed embedding vector.
    """

    evidence: EvidenceItem
    embedding: list[float]


# ---------------------------------------------------------------------------
# EvidenceIndex
# ---------------------------------------------------------------------------

@dataclass
class EvidenceIndex:
    """In-memory index of evidence with embeddings.

    Fields:
    -------
    indexed_items:
        List of IndexedEvidence.
    embedding_model:
        Name/version of embedding model used.
    indexed_count:
        Total number of indexed evidence items.
    """

    indexed_items: list[IndexedEvidence] = field(default_factory=list)
    embedding_model: str = "unknown"
    indexed_count: int = 0

    def get_evidence_by_id(self, evidence_id: str) -> Optional[EvidenceItem]:
        """Look up evidence item by evidence_id.

        Parameters
        ----------
        evidence_id:
            The evidence ID to find.

        Returns
        -------
        Optional[EvidenceItem]
            The evidence item, or None if not found.
        """
        for item in self.indexed_items:
            if item.evidence.evidence_id == evidence_id:
                return item.evidence
        return None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class EvidenceIndexService:
    """Builds and maintains an in-memory index of evidence."""

    def __init__(
        self,
        embedding_provider: AbstractEmbeddingProvider,
        embedding_model_name: str = "fake",
    ) -> None:
        """
        Parameters
        ----------
        embedding_provider:
            Instance of AbstractEmbeddingProvider.
        embedding_model_name:
            Name of the embedding model (for metadata tracking).
            Default: "fake".
        """
        self._embedding_provider = embedding_provider
        self._embedding_model_name = embedding_model_name

    def build_index(
        self,
        evidence_items: list[EvidenceItem],
    ) -> EvidenceIndex:
        """Build a fresh index from evidence items.

        Parameters
        ----------
        evidence_items:
            List of EvidenceItem to index.

        Returns
        -------
        EvidenceIndex
            In-memory index with pre-computed embeddings.

        Raises
        ------
        EmbeddingError
            If embedding provider fails.
        """
        indexed_items: list[IndexedEvidence] = []

        # Embed all items
        for item in evidence_items:
            representation = (
                SemanticEvidenceRetrievalService._make_evidence_representation(item)
            )
            embedding = self._embedding_provider.embed_text(representation)
            indexed_items.append(IndexedEvidence(evidence=item, embedding=embedding))

        return EvidenceIndex(
            indexed_items=indexed_items,
            embedding_model=self._embedding_model_name,
            indexed_count=len(indexed_items),
        )
