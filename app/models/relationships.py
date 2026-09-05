"""Internal domain models for the Entity Relationship Intelligence Engine (Stage 12).

These are the authoritative representations of relationships between canonical
entities inside ThreadLine. They are *not* tied to any API schema or persistence
format.

Design notes
------------
- RelationshipType and RelationshipEvidenceType are StrEnums.
- EntityRelationship represents a single deterministic relationship edge.
- EntityRelationshipGraph represents all relationships surrounding one entity.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class RelationshipType(str, Enum):
    """The type of relationship between two entities.
    
    Currently restricted to co-occurrence as there is no deterministic evidence
    for explicit dependencies (e.g., BLOCKED_BY, DEPENDS_ON) in the ingestion data.
    """

    CO_OCCURS_WITH = "CO_OCCURS_WITH"
    RELATED_TO = "RELATED_TO"


class RelationshipEvidenceType(str, Enum):
    """The type of deterministic evidence supporting a relationship."""

    CO_OCCURRENCE = "CO_OCCURRENCE"


# ---------------------------------------------------------------------------
# Domain Models
# ---------------------------------------------------------------------------

class EntityRelationship(BaseModel):
    """A deterministic relationship between two canonical entities.
    
    This model represents a directional edge in the relationship graph. For
    symmetric relationships (like CO_OCCURS_WITH), the engine will deterministically
    deduplicate logic but return edges from the viewpoint of the queried entity.
    """

    # Identity
    relationship_id: str = Field(
        ...,
        description="Deterministic identifier for this relationship (UUID)."
    )

    # Participants
    source_entity_id: str = Field(
        ...,
        description="The entity from which this relationship originates."
    )
    target_entity_id: str = Field(
        ...,
        description="The entity to which this relationship points."
    )

    # Classification
    relationship_type: RelationshipType = Field(
        ...,
        description="The semantic meaning of this relationship."
    )
    evidence_type: RelationshipEvidenceType = Field(
        ...,
        description="The type of evidence that justifies this relationship."
    )

    # Evidence details
    evidence: str = Field(
        ...,
        description="Human-readable summary of the evidence supporting this relationship."
    )
    related_meeting_ids: list[str] = Field(
        default_factory=list,
        description="IDs of meetings providing evidence for this relationship."
    )

    # Metrics
    strength: int = Field(
        ...,
        ge=1,
        description="Deterministic strength score (e.g., meeting co-occurrence count)."
    )
    
    # Sorting
    deterministic_sort_key: str = Field(
        ...,
        description="A stable key ensuring repeatable relationship ordering."
    )


class EntityRelationshipGraph(BaseModel):
    """The relationship intelligence graph surrounding one canonical entity."""

    entity_id: str = Field(..., description="The central entity of this graph.")
    relationships: list[EntityRelationship] = Field(
        default_factory=list,
        description="All deterministic relationships involving this entity."
    )
    related_entity_ids: list[str] = Field(
        default_factory=list,
        description="IDs of all other entities related to this one."
    )
    relationship_count: int = Field(
        ...,
        description="The total number of relationships in this graph."
    )
