"""Internal domain models for the Entity Relationship Intelligence Engine (Stage 12+15).

These are the authoritative representations of relationships between canonical
entities inside ThreadLine. They are *not* tied to any API schema or persistence
format.

Design notes
------------
- RelationshipType and RelationshipEvidenceType are StrEnums.
- EntityRelationship represents a single deterministic relationship edge.
- EntityRelationshipGraph represents all relationships surrounding one entity.
- Stage 15 adds DEPENDS_ON, BLOCKS, and EXPLICIT_STATEMENT to the vocabularies,
  and optional evidence fields (source_text, mention_id) to EntityRelationship.
- CO_OCCURS_WITH behavior is fully preserved and unchanged.

Relationship semantics
----------------------
CO_OCCURS_WITH:
    "These resolved entities were observed in the same meeting."
    Evidence type: CO_OCCURRENCE.  Symmetric.

DEPENDS_ON:
    "Explicit meeting evidence states that source depends on target."
    Evidence type: EXPLICIT_STATEMENT.  Directional.
    source → DEPENDS_ON → target means source requires target.

BLOCKS:
    "Explicit meeting evidence states that source is blocking target."
    Evidence type: EXPLICIT_STATEMENT.  Directional.
    source → BLOCKS → target means source is the blocker, target is blocked.

IMPORTANT: CO_OCCURS_WITH is NEVER promoted to DEPENDS_ON or BLOCKS.
Only explicit textual evidence may produce dependency relationships.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class RelationshipType(str, Enum):
    """The type of relationship between two entities.

    Values
    ------
    CO_OCCURS_WITH:
        Entities were observed in the same meeting transcript.
        Symmetric; evidence type: CO_OCCURRENCE.

    DEPENDS_ON:
        Source entity explicitly depends on target entity.
        Directional; evidence type: EXPLICIT_STATEMENT.

    BLOCKS:
        Source entity is explicitly blocking target entity.
        Directional; evidence type: EXPLICIT_STATEMENT.

    RELATED_TO:
        Generic association (reserved, not currently produced).
    """

    CO_OCCURS_WITH = "CO_OCCURS_WITH"
    DEPENDS_ON = "DEPENDS_ON"
    BLOCKS = "BLOCKS"
    RELATED_TO = "RELATED_TO"


class RelationshipEvidenceType(str, Enum):
    """The type of deterministic evidence supporting a relationship.

    Values
    ------
    CO_OCCURRENCE:
        The entities appeared together in the same meeting.

    EXPLICIT_STATEMENT:
        A verbatim or near-verbatim excerpt from a meeting transcript
        explicitly states the relationship (DEPENDS_ON or BLOCKS).
    """

    CO_OCCURRENCE = "CO_OCCURRENCE"
    EXPLICIT_STATEMENT = "EXPLICIT_STATEMENT"


# ---------------------------------------------------------------------------
# Domain Models
# ---------------------------------------------------------------------------

class EntityRelationship(BaseModel):
    """A deterministic relationship between two canonical entities.

    This model represents a directional edge in the relationship graph. For
    symmetric relationships (like CO_OCCURS_WITH), the engine will deterministically
    deduplicate logic but return edges from the viewpoint of the queried entity.

    For explicit relationships (DEPENDS_ON, BLOCKS), source_entity_id and
    target_entity_id are meaningful and directional — they must never be swapped.
    """

    # Identity
    relationship_id: str = Field(
        ...,
        description="Deterministic identifier for this relationship.",
    )

    # Participants
    source_entity_id: str = Field(
        ...,
        description="The entity from which this relationship originates.",
    )
    target_entity_id: str = Field(
        ...,
        description="The entity to which this relationship points.",
    )

    # Classification
    relationship_type: RelationshipType = Field(
        ...,
        description="The semantic meaning of this relationship.",
    )
    evidence_type: RelationshipEvidenceType = Field(
        ...,
        description="The type of evidence that justifies this relationship.",
    )

    # Evidence details
    evidence: str = Field(
        ...,
        description="Human-readable summary of the evidence supporting this relationship.",
    )
    related_meeting_ids: list[str] = Field(
        default_factory=list,
        description="IDs of meetings providing evidence for this relationship.",
    )

    # Optional traceability for explicit relationships
    source_text: Optional[str] = Field(
        default=None,
        description=(
            "For EXPLICIT_STATEMENT relationships: the verbatim transcript excerpt "
            "that most strongly supports this relationship.  None for CO_OCCURS_WITH."
        ),
    )
    mention_id: Optional[str] = Field(
        default=None,
        description=(
            "For EXPLICIT_STATEMENT relationships: the ID of the resolved mention "
            "whose source_text contains the primary evidence.  None for CO_OCCURS_WITH."
        ),
    )

    # Metrics
    strength: int = Field(
        ...,
        ge=1,
        description=(
            "Number of distinct meetings that contain evidence for this relationship. "
            "For CO_OCCURS_WITH: co-occurrence count.  "
            "For DEPENDS_ON/BLOCKS: count of meetings with explicit statements."
        ),
    )

    # Sorting
    deterministic_sort_key: str = Field(
        ...,
        description="A stable key ensuring repeatable relationship ordering.",
    )


class EntityRelationshipGraph(BaseModel):
    """The relationship intelligence graph surrounding one canonical entity."""

    entity_id: str = Field(..., description="The central entity of this graph.")
    relationships: list[EntityRelationship] = Field(
        default_factory=list,
        description="All deterministic relationships involving this entity.",
    )
    related_entity_ids: list[str] = Field(
        default_factory=list,
        description="IDs of all other entities related to this one.",
    )
    relationship_count: int = Field(
        ...,
        description="The total number of relationships in this graph.",
    )
