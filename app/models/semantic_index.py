"""Persistent semantic index record model (Stage 20).

Represents a single embedding in the persistent semantic index.

Identity:
  - evidence_id: the source evidence item
  - embedding_model: model name (e.g., "text-embedding-3-small")
  - representation_version: version of representation scheme

Key fields:
  - representation_hash: SHA256 of the embedding representation
    Used to detect if evidence has changed.
  - embedding_dimension: size of the embedding vector
  - indexed_at: when this record was created/updated (INDEX METADATA)
    NOT event time; use for diagnostics only.

Persistence model:
  - Derived data (never source of truth)
  - Can be rebuilt from source evidence
  - Can be deleted without losing organisational data
  - Can be inconsistent with source (stale records possible)
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SemanticIndexRecord(BaseModel):
    """A persistent semantic index record.

    Represents a single embedding and its metadata.
    Primary key: (evidence_id, embedding_model, representation_version).

    Fields
    ------
    evidence_id
        The evidence item this embedding represents.
    embedding
        The embedding vector (list of floats).
    embedding_model
        Name of the embedding model used (e.g., "text-embedding-3-small", "fake").
    embedding_dimension
        Size of the embedding vector.
    representation_hash
        SHA256 hash of the evidence representation used for embedding.
        Used to detect if evidence has changed.
    representation_version
        Version of the representation scheme (e.g., "1.0").
        If representation logic changes, increment this version.
        Old embeddings with old version can be recomputed.
    source_reference
        Human-readable reference to the source evidence provenance.
    indexed_at
        Timestamp when this record was created or last updated.
        INDEX METADATA ONLY. Not evidence event time.
    """

    evidence_id: str = Field(
        ...,
        description="The evidence item this embedding represents.",
    )

    embedding: list[float] = Field(
        ...,
        description="The embedding vector.",
    )

    embedding_model: str = Field(
        ...,
        description="Name of the embedding model used.",
    )

    embedding_dimension: int = Field(
        ...,
        gt=0,
        description="Size of the embedding vector.",
    )

    representation_hash: str = Field(
        ...,
        description="SHA256 hash of the evidence representation. Used for change detection.",
    )

    representation_version: str = Field(
        default="1.0",
        description="Version of the representation scheme.",
    )

    source_reference: Optional[str] = Field(
        default=None,
        description="Human-readable provenance reference.",
    )

    indexed_at: datetime = Field(
        ...,
        description="Index metadata: when this record was created/updated.",
    )

    source_revision: int | None = Field(
        default=None,
        description=(
            "Authoritative meeting source revision this semantic evidence was "
            "derived from. None for legacy records written before revision "
            "tracking. A record from revision N must never masquerade as "
            "current revision N+1."
        ),
    )

    meeting_id: str | None = Field(
        default=None,
        description=(
            "Source meeting this semantic evidence was derived from, when known. "
            "Used for meeting-scoped consistency checks and incremental indexing."
        ),
    )

    def __hash__(self):
        """Hash by composite identity."""
        return hash((self.evidence_id, self.embedding_model, self.representation_version))

    def __eq__(self, other):
        """Equality by composite identity (not full record)."""
        if not isinstance(other, SemanticIndexRecord):
            return False
        return (
            self.evidence_id == other.evidence_id
            and self.embedding_model == other.embedding_model
            and self.representation_version == other.representation_version
        )
