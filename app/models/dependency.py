"""Internal domain models for Explicit Dependency Intelligence (Stage 15).

An ExplicitDependency represents a dependency or blocking relationship between
two canonical entities that is directly supported by verbatim meeting evidence.

Design notes
------------
- ExplicitDependency is a SINGLE evidence record tied to one meeting and one
  mention.  The relationship service aggregates these into logical
  EntityRelationship edges with strength = distinct meeting count.
- dependency_id is deterministic: sha256(source_id:target_id:type:meeting_id)[:16]
  This ensures the same evidence from the same meeting always produces the
  same record, enabling safe re-processing.
- relationship_type is restricted to DEPENDS_ON and BLOCKS.
  CO_OCCURS_WITH is NEVER stored here — it is computed on-the-fly by
  EntityRelationshipService from meeting co-occurrence.
- source_text is the verbatim or near-verbatim excerpt that explicitly
  supports the dependency.  It must never be fabricated.
- This model is entirely separate from EntityImpact (Stage 13). An
  ExplicitDependency is a relationship fact; an EntityImpact is a risk signal.

IMPORTANT ARCHITECTURAL CONSTRAINT:
An ExplicitDependency does NOT imply causality.  It records that a relationship
statement was explicitly made in a meeting.  It does NOT prove that one entity
will definitely block or cause another to fail.
"""

from typing import Optional

from pydantic import BaseModel, Field

from app.models.relationships import RelationshipEvidenceType, RelationshipType


class ExplicitDependency(BaseModel):
    """A single, evidence-backed explicit dependency between two canonical entities.

    This record ties one relationship assertion to one meeting and one mention.
    Multiple ExplicitDependency records for the same logical relationship
    (same source + target + type, different meetings) are aggregated by
    EntityRelationshipService into a single EntityRelationship with
    strength = distinct meeting count.

    Fields
    ------
    dependency_id:
        Deterministic identifier.
        sha256(f"{source_entity_id}:{target_entity_id}:{relationship_type}:{meeting_id}")[:16]
    source_entity_id:
        The entity that holds the dependency or does the blocking.
        For DEPENDS_ON: the entity that depends on target.
        For BLOCKS: the entity that is blocking target.
    target_entity_id:
        The entity being depended on or being blocked.
    relationship_type:
        Must be DEPENDS_ON or BLOCKS.  Never CO_OCCURS_WITH.
    evidence_type:
        Always EXPLICIT_STATEMENT for ExplicitDependency records.
    source_text:
        The verbatim or near-verbatim excerpt from the meeting transcript
        that explicitly states the relationship.
    meeting_id:
        The meeting where this relationship statement was observed.
    mention_id:
        The resolved entity mention whose source_text contains the statement.
    """

    dependency_id: str = Field(
        ...,
        description=(
            "Deterministic identifier: "
            "sha256(source_id:target_id:rel_type:meeting_id)[:16]."
        ),
    )

    source_entity_id: str = Field(
        ...,
        description=(
            "Entity that holds the dependency (DEPENDS_ON) or causes blocking (BLOCKS)."
        ),
    )

    target_entity_id: str = Field(
        ...,
        description=(
            "Entity being depended on (DEPENDS_ON) or being blocked (BLOCKS)."
        ),
    )

    relationship_type: RelationshipType = Field(
        ...,
        description="DEPENDS_ON or BLOCKS.  CO_OCCURS_WITH is never stored here.",
    )

    evidence_type: RelationshipEvidenceType = Field(
        default=RelationshipEvidenceType.EXPLICIT_STATEMENT,
        description="Always EXPLICIT_STATEMENT for ExplicitDependency records.",
    )

    source_text: str = Field(
        ...,
        description=(
            "Verbatim or near-verbatim excerpt from the meeting transcript "
            "that explicitly states the relationship.  Never fabricated."
        ),
    )

    meeting_id: str = Field(
        ...,
        description="ID of the meeting where this relationship was stated.",
    )

    mention_id: str = Field(
        ...,
        description="ID of the resolved entity mention containing the evidence.",
    )

    source_revision: int = Field(
        default=1,
        ge=1,
        description=(
            "Authoritative meeting source revision this dependency was derived from. "
            "Prevents revision-N derived state from masquerading as current revision N+1."
        ),
    )
