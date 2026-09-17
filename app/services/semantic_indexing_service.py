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
from typing import Callable

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
        ownership_checker=None,
        current_revision_lookup: "Callable[[str | None], int | None] | None" = None,
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
        ownership_checker:
            Optional callable raising on stale worker ownership loss.
            Checked before every durable semantic write so a stale worker
            cannot commit conflicting semantic state.
        current_revision_lookup:
            Callable mapping an optional meeting_id to its authoritative
            current source revision (int).  When provided, every semantic
            index write is validated against the meeting's authoritative
            source revision at write time: incoming < current → stale,
            incoming > current → future (would masquerade as current).
            None lookup → guard skipped (legacy/unknown meeting).
        """
        self._embedding_provider = embedding_provider
        self._repository = repository
        self._embedding_model_name = embedding_model_name
        self._representation_version = representation_version
        self._ownership_checker = ownership_checker
        self._current_revision_lookup = current_revision_lookup

    def set_ownership_checker(self, checker) -> None:
        self._ownership_checker = checker

    def _assert_owned(self) -> None:
        if self._ownership_checker is not None:
            self._ownership_checker()

    def index_evidence(
        self,
        evidence_item: EvidenceItem,
        source_revision: int | None = None,
        meeting_id: str | None = None,
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

        # Stale-revision guard inputs (shared by reuse + write paths).
        incoming_revision = source_revision
        if incoming_revision is None:
            incoming_revision = getattr(evidence_item, "source_revision", None)
        # meeting_id is carried on EvidenceItem.meeting_id; prefer explicit arg.
        resolved_meeting_id = meeting_id if meeting_id is not None else getattr(evidence_item, "meeting_id", None)
        if incoming_revision is not None:
            try:
                incoming_revision = int(incoming_revision)
            except (TypeError, ValueError):
                incoming_revision = None

        # Authoritative meeting source revision guard (P6/P8):
        # Validates against the meeting's current source revision at write
        # time, catching both stale (incoming < current) and future
        # (incoming > current, would masquerade as current) semantic writes.
        if incoming_revision is not None and self._current_revision_lookup is not None:
            current_meeting_rev = self._current_revision_lookup(resolved_meeting_id)
            if current_meeting_rev is not None:
                if incoming_revision < current_meeting_rev:
                    from app.repositories.background_job_repository import (
                        StaleJobOwnershipError,
                    )

                    raise StaleJobOwnershipError(
                        f"stale semantic write rejected for {evidence_item.evidence_id}: "
                        f"incoming revision {incoming_revision} < authoritative meeting revision {current_meeting_rev}"
                    )
                if incoming_revision > current_meeting_rev:
                    from app.services.processing_consistency_service import (
                        FutureRevisionError,
                    )

                    raise FutureRevisionError(
                        f"future semantic write rejected for {evidence_item.evidence_id}: "
                        f"incoming revision {incoming_revision} > authoritative meeting revision {current_meeting_rev}"
                    )

        if existing and existing.representation_hash == representation_hash:
            # Embedding is still valid; reuse the vector.  When the incoming
            # source revision is newer, advance the durable revision marker
            # (same embedding, new currency) so revision N never masquerades
            # as N+1 while still avoiding a re-embed.
            existing_revision = getattr(existing, "source_revision", None)
            needs_advance = (
                incoming_revision is not None
                and (existing_revision is None or int(incoming_revision) != int(existing_revision))
            )
            if not needs_advance:
                logger.debug(
                    f"Reusing embedding for {evidence_item.evidence_id} "
                    f"(hash unchanged)"
                )
                return existing
            if existing_revision is not None and int(incoming_revision) < int(existing_revision):
                from app.repositories.background_job_repository import (
                    StaleJobOwnershipError,
                )

                raise StaleJobOwnershipError(
                    f"stale semantic write rejected for {evidence_item.evidence_id}: "
                    f"incoming revision {incoming_revision} < durable revision {existing_revision}"
                )
            self._assert_owned()
            updated = existing.model_copy(update={
                "source_revision": int(incoming_revision),
                "meeting_id": resolved_meeting_id if resolved_meeting_id is not None else existing.meeting_id,
                "indexed_at": datetime.now(timezone.utc),
            })
            persisted = self._repository.upsert(updated)
            logger.debug(f"Advanced semantic revision for {evidence_item.evidence_id} to {incoming_revision}")
            return persisted

        if existing is not None and incoming_revision is not None:
            existing_revision = getattr(existing, "source_revision", None)
            if existing_revision is not None and int(incoming_revision) < int(existing_revision):
                from app.repositories.background_job_repository import (
                    StaleJobOwnershipError,
                )

                raise StaleJobOwnershipError(
                    f"stale semantic write rejected for {evidence_item.evidence_id}: "
                    f"incoming revision {incoming_revision} < durable revision {existing_revision}"
                )

        # Ownership guard before any durable mutation: a worker that lost its
        # lease must not commit semantic state.
        self._assert_owned()

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
            source_revision=int(incoming_revision) if incoming_revision is not None else None,
            meeting_id=resolved_meeting_id,
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

    def retry_indexing(self, evidence_item: EvidenceItem) -> SemanticIndexRecord:
        """Retry indexing from an authoritative source evidence item.

        This operation is intentionally source-item based: the semantic index
        stores no text and cannot be used to reconstruct missing evidence.
        Existing valid records remain untouched until embedding generation and
        the atomic repository upsert both succeed.
        """
        return self.index_evidence(evidence_item)

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
        source_evidence: list[EvidenceItem] | None = None,
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

        source_by_id = {
            item.evidence_id: item for item in (source_evidence or [])
        }

        records_by_model: dict[str, int] = {}
        for record in records:
            records_by_model[record.embedding_model] = (
                records_by_model.get(record.embedding_model, 0) + 1
            )

        # Check for invalid vectors
        for record in records:
            if source_evidence is not None and record.evidence_id not in source_by_id:
                issues.append(f"Missing source evidence for {record.evidence_id}")

            if source_evidence is not None and record.evidence_id in source_by_id:
                representation = SemanticEvidenceRetrievalService._make_evidence_representation(
                    source_by_id[record.evidence_id]
                )
                expected_hash = self._compute_representation_hash(representation)
                if record.representation_hash != expected_hash:
                    issues.append(f"Stale representation hash for {record.evidence_id}")

            if record.embedding_model != self._embedding_model_name:
                issues.append(
                    f"Unexpected embedding model for {record.evidence_id}: "
                    f"{record.embedding_model}"
                )
            if record.representation_version != self._representation_version:
                issues.append(
                    f"Unexpected representation version for {record.evidence_id}: "
                    f"{record.representation_version}"
                )

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
