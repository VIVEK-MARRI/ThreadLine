"""Pydantic schemas for the Temporal State Engine API.

These are the *public contract* of the timeline endpoint — what clients
receive.  They are deliberately kept separate from the internal domain
models (app/models/temporal.py) so the API surface can remain stable
while the internal models evolve.

Design notes
------------
- TemporalStateSchema mirrors TemporalState but is independent so the two
  layers can diverge in the future without a breaking API change.
- StateObservationSchema mirrors StateObservation but uses only API-safe
  types (no internal domain imports that could couple the layers).
- EntityTimelineResponse includes convenience count fields so clients
  can quickly see summary statistics without iterating the timeline list.
- All optional fields are avoided where possible; every field has a
  definite, meaningful value.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.entity import EntityTypeSchema


# ---------------------------------------------------------------------------
# Controlled vocabulary (API layer copy)
# ---------------------------------------------------------------------------

class TemporalStateSchema(str, Enum):
    """Lifecycle state values returned by the timeline API.

    Mirrors the domain model's TemporalState enum.
    Kept as an independent enum so the API surface can evolve separately
    from the internal model if needed.

    UNKNOWN     — no state-bearing evidence found.
    OPEN        — issue raised/identified but not yet started.
    IN_PROGRESS — actively being worked on.
    BLOCKED     — blocked, stalled, or waiting.
    RESOLVED    — completed, fixed, or closed.
    """

    UNKNOWN = "UNKNOWN"
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    RESOLVED = "RESOLVED"


# ---------------------------------------------------------------------------
# Per-observation schema
# ---------------------------------------------------------------------------

class StateObservationSchema(BaseModel):
    """A single interpreted observation in the temporal timeline.

    Returned as an element of EntityTimelineResponse.timeline.
    Every field is included to keep the API evidence-backed and auditable.
    """

    observation_index: int = Field(
        ...,
        ge=0,
        description=(
            "Zero-based position of this observation in the chronological timeline."
        ),
    )
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
        ..., description="ID of the resolved EntityMention that produced this observation."
    )
    evidence_text: str = Field(
        ...,
        description=(
            "The surrounding transcript excerpt used to interpret the state.  "
            "This is the source_text of the EntityMention."
        ),
    )
    interpreted_state: TemporalStateSchema = Field(
        ...,
        description=(
            "The lifecycle state inferred from evidence_text.  "
            "UNKNOWN when no state-bearing keywords were found."
        ),
    )
    transition_occurred: bool = Field(
        ...,
        description=(
            "True when this observation caused a valid state transition.  "
            "False for repeated states or invalid transitions."
        ),
    )
    from_state: TemporalStateSchema = Field(
        ...,
        description="The state immediately before this observation was processed.",
    )
    to_state: TemporalStateSchema = Field(
        ...,
        description=(
            "The state after this observation was processed.  "
            "Equals from_state when no transition occurred."
        ),
    )
    is_valid_transition: bool = Field(
        ...,
        description=(
            "True when the transition from from_state to interpreted_state is "
            "permitted.  False for invalid transitions (e.g., RESOLVED to IN_PROGRESS)."
        ),
    )
    transition_skipped_reason: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable explanation of why the transition was not applied.  "
            "Null when is_valid_transition is true."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "observation_index": 0,
                    "meeting_id": "meeting_001",
                    "meeting_title": "Sprint Planning",
                    "meeting_date": "2026-08-21T10:00:00Z",
                    "mention_id": "mention_abc",
                    "evidence_text": "The payment API issue has started being investigated.",
                    "interpreted_state": "IN_PROGRESS",
                    "transition_occurred": True,
                    "from_state": "UNKNOWN",
                    "to_state": "IN_PROGRESS",
                    "is_valid_transition": True,
                    "transition_skipped_reason": None,
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Timeline response schema
# ---------------------------------------------------------------------------

class EntityTimelineResponse(BaseModel):
    """Response body for GET /api/v1/entities/{entity_id}/timeline.

    Returns the temporal lifecycle history of a canonical entity: all resolved
    observations (mentions) interpreted for lifecycle state, ordered
    chronologically by meeting_date, with transition metadata for each
    observation.

    Always returns HTTP 200 when the entity exists.
    Returns HTTP 404 if the entity_id does not exist.
    Returns an empty timeline (observation_count=0, current_state='UNKNOWN')
    when the entity exists but has no resolved mentions.

    Ordering: timeline entries are sorted by
      (meeting_date ASC, meeting_id ASC, mention_id ASC).
    This ordering is deterministic and uses only real data fields — identical
    to the ordering guaranteed by the Cross-Meeting Correlation endpoint.

    Read-only invariant: This endpoint never creates or modifies entities,
    mentions, or resolution state.  It never re-runs extraction, candidate
    generation, scoring, or resolution.
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
    current_state: TemporalStateSchema = Field(
        ...,
        description=(
            "The current (most recent) lifecycle state of this entity.  "
            "UNKNOWN when no state-bearing evidence was found."
        ),
    )
    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Total number of resolved observations for this entity.  "
            "Equals len(timeline).  Provided as a convenience field."
        ),
    )
    transition_count: int = Field(
        ...,
        ge=0,
        description=(
            "Number of valid state transitions that occurred.  "
            "Equals the count of timeline entries where transition_occurred is true."
        ),
    )
    timeline: list[StateObservationSchema] = Field(
        default_factory=list,
        description=(
            "Chronologically ordered list of all interpreted observations.  "
            "Empty when no resolved mentions exist.  "
            "Ordered by (meeting_date ASC, meeting_id ASC, mention_id ASC)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "entity_id": "entity_001",
                    "canonical_name": "payment api instability",
                    "entity_type": "ISSUE",
                    "current_state": "RESOLVED",
                    "observation_count": 3,
                    "transition_count": 2,
                    "timeline": [
                        {
                            "observation_index": 0,
                            "meeting_id": "meeting_001",
                            "meeting_title": "Sprint Planning",
                            "meeting_date": "2026-08-21T10:00:00Z",
                            "mention_id": "mention_abc",
                            "evidence_text": "The issue has started being investigated.",
                            "interpreted_state": "IN_PROGRESS",
                            "transition_occurred": True,
                            "from_state": "UNKNOWN",
                            "to_state": "IN_PROGRESS",
                            "is_valid_transition": True,
                            "transition_skipped_reason": None,
                        },
                        {
                            "observation_index": 1,
                            "meeting_id": "meeting_002",
                            "meeting_title": "Weekly Sync",
                            "meeting_date": "2026-08-28T10:00:00Z",
                            "mention_id": "mention_def",
                            "evidence_text": "The issue is blocked on infrastructure access.",
                            "interpreted_state": "BLOCKED",
                            "transition_occurred": True,
                            "from_state": "IN_PROGRESS",
                            "to_state": "BLOCKED",
                            "is_valid_transition": True,
                            "transition_skipped_reason": None,
                        },
                        {
                            "observation_index": 2,
                            "meeting_id": "meeting_003",
                            "meeting_title": "Retrospective",
                            "meeting_date": "2026-09-04T10:00:00Z",
                            "mention_id": "mention_ghi",
                            "evidence_text": "The payment API issue has been resolved.",
                            "interpreted_state": "RESOLVED",
                            "transition_occurred": True,
                            "from_state": "BLOCKED",
                            "to_state": "RESOLVED",
                            "is_valid_transition": True,
                            "transition_skipped_reason": None,
                        },
                    ],
                },
                {
                    "entity_id": "entity_002",
                    "canonical_name": "payment api instability",
                    "entity_type": "ISSUE",
                    "current_state": "UNKNOWN",
                    "observation_count": 0,
                    "transition_count": 0,
                    "timeline": [],
                },
            ]
        }
    }
