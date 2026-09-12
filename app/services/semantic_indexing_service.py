"""Semantic indexing service with incremental embedding pipeline (Stage 20).

Responsible for:
1. Computing deterministic evidence representations
2. Computing representation hashes for change detection
3. Checking if embeddings can be reused
4. Generating new embeddings when needed
5. Persisting to semantic index repository
6. Handling incremental batch operations
7. Providing consistency checks
8. Supporting rebuild operations

Design:
  - Cost control: reuse embeddings for unchanged evidence
  - Change detection: via representation hash
  - Model safety: never mix embeddings from different models
  - Idempotency: calling twice with same evidence doesn't duplicate
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from app.models.natural_language import EvidenceItem
from app.models.semantic_index import SemanticIndexRecord
from app.providers.embedding_base import (
    AbstractEmbeddingProvider,
    EmbeddingError,
)
from app.repositories.semantic_index_repository import AbstractSemanticIndexRepository
from app.services.semantic_evidence_retrieval_service import (
    SemanticEvidenceRetrievalService,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Representation and Hash
# ---------------------------------------------------------------------------

class SemanticIndexingService:
    """Manages persistent semantic index with incremental updates."""

    def __init__(
        self,
        embedding_provider: AbstractEmbeddingProvider,
        repository: AbstractSemanticIndexRepository,
        embedding_model_name: str = "fake",
        representation_version: str = "1.0",
    ) -> None:
        """
        Parameters
        ----------
        embedding_provider:
            Instance of AbstractEmbeddingProvider.
        repository:
            Instance of AbstractSemanticIndexRepository.
        embedding_model_name:
            Name of the embedding model. Default: "fake".
        representation_version:
            Version of the representation scheme. Default: "1.0".
            Increment this when representation logic changes.
        """
        self._embedding_provider = embedding_provider
        self._repository = repository
        self._embedding_model_name = embedding_model_name
        self._representation_version = representation_version

    def index_evidence(
        self,
        evidence_item: EvidenceItem,
    ) -> SemanticIndexRecord:
        """Index a single evidence item.

        If a valid embedding already exists (same representation),
        reuses it. Otherwise, generates and persists a new embedding.

        Parameters
        ----------
        evidence_item:
            The evidence to index.

        Returns
        -------
        SemanticIndexRecord
            The persisted index record.

        Raises
        ------
        EmbeddingError
            If embedding generation fails.
        """
        # 1. Compute deterministic representation
        representation = (
            SemanticEvidenceRetrievalService._make_evidence_representation(
                evidence_item
            )
        )

        # 2. Compute representation hash
        representation_hash = self._compute_representation_hash(representation)

        # 3. Check if valid record exists
        existing = self._repository.get_by_composite_key(
            evidence_id=evidence_item.evidence_id,
            embedding_model=self._embedding_model_name,
            representation_version=self._representation_version,
        )

        if existing and existing.representation_hash == representation_hash:
            # Embedding is still valid; reuse it
            logger.debug(
                f"Reusing embedding for {evidence_item.evidence_id} "
                f"(hash unchanged)"
            )
            return existing

        # 4. Generate new embedding
        embedding = self._embedding_provider.embed_text(representation)

        # 5. Create and persist record
        record = SemanticIndexRecord(
            evidence_id=evidence_item.evidence_id,
            embedding=embedding,
            embedding_model=self._embedding_model_name,
            embedding_dimension=len(embedding),
            representation_hash=representation_hash,
            representation_version=self._representation_version,
            source_reference=evidence_item.source_reference,
            indexed_at=datetime.now(timezone.utc),
        )

        persisted = self._repository.upsert(record)
        logger.debug(f"Indexed {evidence_item.evidence_id}")
        return persisted

    def index_evidence_batch(
        self,
        evidence_items: list[EvidenceItem],
    ) -> list[SemanticIndexRecord]:
        """Index multiple evidence items.

        Processes each item; individual failures don't block others.
        Returns only successfully indexed records.

        Parameters
        ----------
        evidence_items:
            List of evidence to index.

        Returns
        -------
        list[SemanticIndexRecord]
            Successfully indexed records.
        """
        results = []
        for item in evidence_items:
            try:
                record = self.index_evidence(item)
                results.append(record)
            except EmbeddingError as e:
                logger.error(
                    f"Failed to index {item.evidence_id}: {e}"
                )
                # Continue with next item (partial success)
        return results

    def refresh_if_changed(
        self,
        evidence_item: EvidenceItem,
    ) -> tuple[SemanticIndexRecord, bool]:
        """Index if changed, otherwise return existing record.

        Parameters
        ----------
        evidence_item:
            The evidence to check and potentially re-index.

        Returns
        -------
        tuple[SemanticIndexRecord, bool]
            (record, was_changed)
            was_changed=True if a new embedding was generated.

        Raises
        ------
        EmbeddingError
            If embedding generation fails.
        """
        # Compute current hash
        representation = (
            SemanticEvidenceRetrievalService._make_evidence_representation(
                evidence_item
            )
        )
        current_hash = self._compute_representation_hash(representation)

        # Get existing record
        existing = self._repository.get_by_composite_key(
            evidence_id=evidence_item.evidence_id,
            embedding_model=self._embedding_model_name,
            representation_version=self._representation_version,
        )

        if existing and existing.representation_hash == current_hash:
            # No change
            return (existing, False)

        # Change detected; re-index
        record = self.index_evidence(evidence_item)
        return (record, True)

    def remove_evidence(
        self,
        evidence_id: str,
    ) -> int:
        """Remove all semantic index records for an evidence item.

        Removes ALL embeddings for this evidence_id across all models/versions.
        This is safe because embeddings are derived data.

        Parameters
        ----------
        evidence_id:
            The evidence identifier.

        Returns
        -------
        int
            Number of records deleted.
        """
        count = self._repository.delete_by_evidence_id(evidence_id)
        logger.debug(f"Removed {count} index records for {evidence_id}")
        return count

    def rebuild_index(
        self,
        evidence_items: list[EvidenceItem],
    ) -> int:
        """Rebuild semantic index from evidence list.

        Safe to run repeatedly. Does not corrupt source evidence.
        Deletes stale records for items no longer in source list.

        Parameters
        ----------
        evidence_items:
            Current list of evidence to index.

        Returns
        -------
        int
            Number of records successfully indexed.
        """
        # Build set of current evidence IDs
        current_ids = {item.evidence_id for item in evidence_items}

        # Get all records for current model/version
        all_records = self._repository.list_by_model(self._embedding_model_name)

        # Find stale records (exist in index but not in source)
        stale_ids = {
            r.evidence_id
            for r in all_records
            if r.representation_version == self._representation_version
            and r.evidence_id not in current_ids
        }

        # Delete stale records
        for stale_id in stale_ids:
            self._repository.delete_by_evidence_id(stale_id)
            logger.info(f"Deleted stale records for {stale_id}")

        # Index all current evidence
        results = self.index_evidence_batch(evidence_items)
        logger.info(f"Rebuild complete: indexed {len(results)} items")
        return len(results)

    def check_consistency(
        self,
    ) -> dict:
        """Diagnostic check of index consistency.

        Returns a report of potential issues without fixing them.

        Returns
        -------
        dict
            Consistency report with keys:
            - total_records: int
            - records_by_model: dict[str, int]
            - issues: list[str] (detected problems)
        """
        records = self._repository.list_all()
        issues = []

        records_by_model: dict[str, int] = {}
        for record in records:
            records_by_model[record.embedding_model] = (
                records_by_model.get(record.embedding_model, 0) + 1
            )

        # Check for invalid vectors
        for record in records:
            if not record.embedding:
                issues.append(
                    f"Empty embedding for {record.evidence_id} "
                    f"({record.embedding_model}/{record.representation_version})"
                )

            if len(record.embedding) != record.embedding_dimension:
                issues.append(
                    f"Dimension mismatch for {record.evidence_id}: "
                    f"claimed {record.embedding_dimension}, "
                    f"actual {len(record.embedding)}"
                )

            # Check for NaN or infinity
            for i, val in enumerate(record.embedding):
                if not isinstance(val, (int, float)):
                    issues.append(
                        f"Invalid value type at {record.evidence_id}[{i}]: "
                        f"{type(val)}"
                    )
                elif val != val:  # NaN check
                    issues.append(
                        f"NaN value at {record.evidence_id}[{i}]"
                    )
                elif val == float('inf') or val == float('-inf'):
                    issues.append(
                        f"Infinity value at {record.evidence_id}[{i}]"
                    )

        return {
            "total_records": len(records),
            "records_by_model": records_by_model,
            "issues": issues,
        }

    @staticmethod
    def _compute_representation_hash(representation: str) -> str:
        """Compute SHA256 hash of evidence representation.

        Parameters
        ----------
        representation:
            Evidence representation string.

        Returns
        -------
        str
            SHA256 hex digest (full 64 chars).
        """
        return hashlib.sha256(representation.encode("utf-8")).hexdigest()
