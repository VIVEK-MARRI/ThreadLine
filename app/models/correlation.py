"""Internal domain read-models for Cross-Meeting Correlation.

These models are the authoritative representations of correlation results
inside Threadline.  They are *not* tied to any API schema or persistence
format — those layers translate to/from these models as needed.

Design notes
------------
- EntityObservation is a lightweight read-model, not a stored record.
  It joins EntityMention with Meeting metadata (title, date) so that the
  correlation result is self-contained without requiring callers to do
  additional repository lookups.
- EntityCorrelation is the top-level aggregation result for a canonical
  entity across all meetings.  It is computed on read and never persisted.
- Neither model duplicates the EntityMention or Meeting domain models;
  they select and combine only the fields meaningful for correlation.
- entity_type uses EntityType (the domain enum) rather than a string so
  the model remains strongly typed within the service layer.

These models are populated exclusively by CorrelationService and must
never be modified by other pipeline stages.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.entity import EntityType


# ---------------------------------------------------------------------------
# Observation — a single resolved mention enriched with meeting metadata
# ---------------------------------------------------------------------------

class EntityObservation(BaseModel):
    """A single observation of a canonical entity within one meeting.

    An observation joins one resolved EntityMention with the metadata of
    the meeting it was observed in.  It is the atomic unit of the
    cross-meeting correlation result.

    Fields that exist on EntityMention are projected directly.
    Fields from Meeting (title, date) are joined in by CorrelationService.

    Example
    -------
    meeting_id:    "meeting_001"
    meeting_title: "Sprint Planning"
    meeting_date:  2026-08-21T10:00:00Z
    mention_id:    "mention_abc"
    mention_text:  "Rahul"
    source_text:   "Rahul will fix the payment API."
    """

    # Meeting provenance
    meeting_id: str = Field(
        ..., description="ID of the meeting where this observation was recorded."
    )
    meeting_title: str = Field(
        ..., description="Human-readable title of the meeting."
    )
    meeting_date: datetime = Field(
        ..., description="Date and time when the meeting took place."
    )

    # Mention details
    mention_id: str = Field(
        ..., description="ID of the resolved EntityMention."
    )
    mention_text: str = Field(
        ...,
        description=(
            "The surface form of the mention as it appeared in the transcript "
            "(e.g. 'Rahul', 'R. Kumar', 'the payment API')."
        ),
    )
    source_text: str = Field(
        ...,
        description=(
            "The surrounding transcript excerpt that contains this mention.  "
            "Provides the evidential context for the observation."
        ),
    )


# ---------------------------------------------------------------------------
# Correlation — the cross-meeting history of a canonical entity
# ---------------------------------------------------------------------------

class EntityCorrelation(BaseModel):
    """The cross-meeting correlation result for a single canonical entity.

    EntityCorrelation aggregates all resolved observations of a canonical
    entity across all meetings it appears in.  It is the output of
    CorrelationService.get_entity_correlations() and is computed on read.

    Observations are ordered chronologically by meeting_date, then
    meeting_id, then mention_id — all deterministic, all from real data.

    Only RESOLVED mentions participate.  AMBIGUOUS and UNRESOLVED mentions
    are excluded (they have no confirmed canonical identity).

    Example
    -------
    entity_id:      "entity_001"
    canonical_name: "rahul kumar"
    entity_type:    EntityType.PERSON
    observations:   [<EntityObservation in Meeting A>, <EntityObservation in Meeting B>]
    """

    # Identity of the canonical entity
    entity_id: str = Field(
        ..., description="Unique identifier of the canonical entity."
    )
    canonical_name: str = Field(
        ..., description="Preferred, normalised name of the canonical entity."
    )
    entity_type: EntityType = Field(
        ..., description="Category of the canonical entity (PERSON, ISSUE, etc.)."
    )

    # Cross-meeting history
    observations: list[EntityObservation] = Field(
        default_factory=list,
        description=(
            "Chronologically ordered list of all resolved observations of this "
            "entity across meetings.  Empty when no resolved mentions exist.  "
            "Ordered by (meeting_date ASC, meeting_id ASC, mention_id ASC)."
        ),
    )
