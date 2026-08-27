"""Internal domain models for the Entity Resolution system.

These are the authoritative representations of canonical entities and entity
mentions inside Threadline.  They are *not* tied to any API schema or
persistence format — those layers translate to/from these models as needed.

Design notes
------------
- EntityType and ResolutionStatus are StrEnums so Pydantic v2 serialises them
  as plain strings (e.g., "PERSON") without any extra model config.
- CanonicalEntity is intentionally separate from EntityMention.
  An entity is an organisational object; a mention is an observed reference.
- entity_id on EntityMention is Optional to support unresolved mentions.
- ResolutionStatus is an enum (not bool) so future states like PENDING_REVIEW
  or AMBIGUOUS can be added without changing the API contract.

Future pipeline stages (fuzzy resolution, embeddings, cross-meeting correlation)
will operate on these models without modifying this module.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class EntityType(str, Enum):
    """The category of real-world object a canonical entity represents.

    Deliberately small for today's foundation.  Add new values here
    (e.g. TEAM, PROJECT, SYSTEM, INITIATIVE) as the system matures.
    """

    PERSON = "PERSON"
    ISSUE = "ISSUE"


class ResolutionStatus(str, Enum):
    """Whether a mention has been matched to a canonical entity.

    RESOLVED  — the mention was matched to a known canonical entity.
    UNRESOLVED — no safe match was found; the mention is stored as-is.

    Future states (not implemented today):
        PENDING_REVIEW — a candidate match exists but requires human confirmation.
        AMBIGUOUS      — multiple plausible matches were found.
        REJECTED       — a proposed match was explicitly declined.
    """

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"


# ---------------------------------------------------------------------------
# Canonical Entity
# ---------------------------------------------------------------------------

class CanonicalEntity(BaseModel):
    """The authoritative record of a real-world organisational object.

    A canonical entity is the single source of truth for a person, issue,
    or other object that Threadline tracks across meetings.  Multiple
    EntityMentions (observed surface forms) may resolve to one entity.

    Example
    -------
    id: "entity_001"
    entity_type: PERSON
    canonical_name: "Rahul Kumar"
    aliases: ["Rahul", "R. Kumar"]
    """

    # Identity
    entity_id: str = Field(..., description="Unique entity identifier (UUID).")

    # Classification
    entity_type: EntityType = Field(..., description="Category of this entity.")

    # Naming
    canonical_name: str = Field(
        ...,
        description=(
            "The preferred, normalised name for this entity.  "
            "Used as the primary key for exact-match resolution."
        ),
    )
    aliases: list[str] = Field(
        default_factory=list,
        description=(
            "Alternative names or surface forms that resolve to this entity.  "
            "Adding an alias does NOT automatically merge other entities."
        ),
    )

    # Metadata – set by the service layer, never by the client
    created_at: datetime = Field(
        ..., description="UTC timestamp when this entity was first created."
    )


# ---------------------------------------------------------------------------
# Entity Mention
# ---------------------------------------------------------------------------

class EntityMention(BaseModel):
    """An observed reference to an entity within a specific meeting transcript.

    A mention captures *how* something was referred to (the surface form)
    and *where* it appeared (meeting + supporting text).  It may or may not
    resolve to a canonical entity.

    Example (resolved)
    ------------------
    text: "Rahul"
    meeting_id: "meeting_123"
    source_text: "Rahul reported that the payment API is unstable."
    entity_id: "entity_001"
    resolution_status: RESOLVED

    Example (unresolved)
    --------------------
    text: "the backend lead"
    meeting_id: "meeting_123"
    source_text: "The backend lead will own this."
    entity_id: None
    resolution_status: UNRESOLVED
    """

    # Identity
    mention_id: str = Field(..., description="Unique mention identifier (UUID).")

    # Classification (denormalised for efficient querying without joining entities)
    entity_type: EntityType = Field(
        ..., description="Entity category this mention is believed to refer to."
    )

    # Surface form
    text: str = Field(
        ..., description="The exact text as it appeared in the transcript."
    )

    # Source context
    meeting_id: str = Field(
        ..., description="ID of the meeting where this mention was observed."
    )
    source_text: str = Field(
        ...,
        description=(
            "The surrounding transcript excerpt that contains this mention.  "
            "Provides evidence for future human review or automated resolution."
        ),
    )

    # Resolution — Optional: None means unresolved
    entity_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the resolved canonical entity, or None if unresolved.  "
            "Absence of a value is meaningful and must never be fabricated."
        ),
    )
    resolution_status: ResolutionStatus = Field(
        default=ResolutionStatus.UNRESOLVED,
        description="Whether this mention has been matched to a canonical entity.",
    )

    # Metadata – set by the service layer
    created_at: datetime = Field(
        ..., description="UTC timestamp when this mention was registered."
    )
