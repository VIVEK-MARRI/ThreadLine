"""Pydantic schemas for the Cross-Meeting Correlation API.

These are the *public contract* of the correlation endpoint — what clients
receive.  They are deliberately kept separate from the internal domain
models (app/models/correlation.py) so the API surface can remain stable
while the internal models evolve.

EntityTypeSchema is imported from the existing entity schema module so
the correlation response uses the same controlled vocabulary.

Design notes
------------
- EntityObservationSchema mirrors EntityObservation (the domain model)
  but is decoupled from it so both can evolve independently.
- EntityCorrelationResponse includes an observation_count convenience
  field so clients can quickly see how many observations exist without
  iterating the list.
- All optional fields are avoided here: every field in a correlation
  response has a definite, meaningful value.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.entity import EntityTypeSchema


# ---------------------------------------------------------------------------
# Observation schema
# ---------------------------------------------------------------------------

class EntityObservationSchema(BaseModel):
    """A single resolved observation of a canonical entity in one meeting.

    This is the API-layer projection of EntityObservation.  It contains
    only the fields meaningful to API consumers — no internal resolution
    state, no raw mention metadata unrelated to the observation.
    """

    meeting_id: str = Field(
        ..., description="ID of the meeting where this observation was recorded."
    )
    meeting_title: str = Field(
        ..., description="Human-readable title of the meeting."
    )
    meeting_date: datetime = Field(
        ..., description="Date and time when the meeting took place (ISO-8601)."
    )
    mention_id: str = Field(
        ..., description="ID of the resolved EntityMention."
    )
    mention_text: str = Field(
        ...,
        description=(
            "The surface form of the mention as it appeared in the transcript "
            "(e.g. 'Rahul', 'the payment API')."
        ),
    )
    source_text: str = Field(
        ...,
        description=(
            "The surrounding transcript excerpt that contains this mention.  "
            "Provides the evidential context for the observation."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "meeting_id": "meeting_001",
                    "meeting_title": "Sprint Planning",
                    "meeting_date": "2026-08-21T10:00:00Z",
                    "mention_id": "mention_abc",
                    "mention_text": "Rahul",
                    "source_text": "Rahul will fix the payment API.",
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Correlation response schema
# ---------------------------------------------------------------------------

class EntityCorrelationResponse(BaseModel):
    """Response body for GET /api/v1/entities/{entity_id}/correlations.

    Returns the cross-meeting history of a canonical entity: all resolved
    observations (mentions) of that entity across all meetings, ordered
    chronologically by meeting_date.

    Always returns HTTP 200 when the entity exists.
    Returns HTTP 404 if the entity_id does not exist.
    Returns an empty observations list (observation_count=0) when the
    entity exists but has no resolved mentions.

    Ordering: observations are sorted by
      (meeting_date ASC, meeting_id ASC, mention_id ASC).
    This ordering is deterministic and uses only real data fields.
    """

    entity_id: str = Field(
        ..., description="Unique identifier of the canonical entity."
    )
    canonical_name: str = Field(
        ..., description="Preferred, normalised name of the canonical entity."
    )
    entity_type: EntityTypeSchema = Field(
        ..., description="Category of the canonical entity (PERSON, ISSUE, etc.)."
    )
    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Total number of resolved observations for this entity.  "
            "Equals len(observations).  Provided as a convenience field."
        ),
    )
    observations: list[EntityObservationSchema] = Field(
        default_factory=list,
        description=(
            "Chronologically ordered list of all resolved observations of this "
            "entity across meetings.  Empty when no resolved mentions exist.  "
            "Ordered by (meeting_date ASC, meeting_id ASC, mention_id ASC)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "entity_id": "entity_001",
                    "canonical_name": "rahul kumar",
                    "entity_type": "PERSON",
                    "observation_count": 2,
                    "observations": [
                        {
                            "meeting_id": "meeting_001",
                            "meeting_title": "Sprint Planning",
                            "meeting_date": "2026-08-21T10:00:00Z",
                            "mention_id": "mention_abc",
                            "mention_text": "Rahul",
                            "source_text": "Rahul will fix the payment API.",
                        },
                        {
                            "meeting_id": "meeting_002",
                            "meeting_title": "Weekly Sync",
                            "meeting_date": "2026-08-28T10:00:00Z",
                            "mention_id": "mention_def",
                            "mention_text": "Rahul Kumar",
                            "source_text": "Rahul Kumar is still investigating the issue.",
                        },
                    ],
                },
                {
                    "entity_id": "entity_002",
                    "canonical_name": "payment api instability",
                    "entity_type": "ISSUE",
                    "observation_count": 0,
                    "observations": [],
                },
            ]
        }
    }
