"""Pydantic schemas for the Organisational Memory API.

These are the *public contract* of the memory endpoint — what clients
receive.  They are deliberately kept separate from the internal domain
models (app/models/memory.py) so the API surface can remain stable
while the internal models evolve.

Design notes
------------
- MemoryFactTypeSchema mirrors MemoryFactType but is independent so the two
  layers can diverge in the future without a breaking API change.
- EntityMemoryFactSchema mirrors EntityMemoryFact but uses only API-safe types.
- EntityMemoryResponse includes convenience count fields so clients can
  quickly check counts without iterating facts.
- All optional fields follow the same Optional[...] = None pattern used
  throughout the schemas layer (correlation.py, temporal.py, entity.py).
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.entity import EntityTypeSchema
from app.schemas.temporal import TemporalStateSchema


# ---------------------------------------------------------------------------
# Controlled vocabulary (API layer copy)
# ---------------------------------------------------------------------------

class MemoryFactTypeSchema(str, Enum):
    """Memory fact type values returned by the memory API.

    Mirrors the domain model's MemoryFactType enum.
    Kept as an independent enum so the API surface can evolve separately
    from the internal model if needed.

    FIRST_OBSERVED       — earliest resolved observation of this entity.
    LAST_OBSERVED        — most recent resolved observation (only when >= 2 exist).
    CURRENT_STATE        — current temporal lifecycle state (aggregate, no evidence).
    STATE_TRANSITION     — a valid lifecycle state transition that occurred.
    REPEATED_OBSERVATION — a meeting where the entity was observed >= 2 times.
    """

    FIRST_OBSERVED = "FIRST_OBSERVED"
    LAST_OBSERVED = "LAST_OBSERVED"
    CURRENT_STATE = "CURRENT_STATE"
    STATE_TRANSITION = "STATE_TRANSITION"
    REPEATED_OBSERVATION = "REPEATED_OBSERVATION"


# ---------------------------------------------------------------------------
# Memory fact schema
# ---------------------------------------------------------------------------

class EntityMemoryFactSchema(BaseModel):
    """A single evidence-backed memory fact about a canonical entity.

    Returned as an element of EntityMemoryResponse.facts.
    Evidence fields are null when not applicable — see MemoryFactTypeSchema
    for which fields are set for each fact type.
    """

    fact_type: MemoryFactTypeSchema = Field(
        ..., description="The category of this memory fact."
    )
    value: str = Field(
        ...,
        description=(
            "The primary value of this fact as a human-readable string.  "
            "FIRST_OBSERVED / LAST_OBSERVED: ISO-8601 datetime.  "
            "CURRENT_STATE: state name (e.g. 'BLOCKED').  "
            "STATE_TRANSITION: 'FROM → TO' (e.g. 'UNKNOWN → IN_PROGRESS').  "
            "REPEATED_OBSERVATION: observation count in that meeting."
        ),
    )
    source_meeting_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the meeting that produced this fact.  "
            "Null for CURRENT_STATE (aggregate fact, not meeting-specific)."
        ),
    )
    source_mention_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the EntityMention that produced this fact.  "
            "Null for CURRENT_STATE and REPEATED_OBSERVATION."
        ),
    )
    observed_at: Optional[datetime] = Field(
        default=None,
        description=(
            "The meeting_date of the meeting where this fact was observed.  "
            "Null for CURRENT_STATE."
        ),
    )
    detail: Optional[str] = Field(
        default=None,
        description=(
            "Additional human-readable context.  "
            "For observation-backed facts: the meeting title.  "
            "Null for CURRENT_STATE."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "fact_type": "FIRST_OBSERVED",
                    "value": "2026-08-01T10:00:00+00:00",
                    "source_meeting_id": "meeting_001",
                    "source_mention_id": "mention_abc",
                    "observed_at": "2026-08-01T10:00:00Z",
                    "detail": "Sprint Planning",
                },
                {
                    "fact_type": "CURRENT_STATE",
                    "value": "BLOCKED",
                    "source_meeting_id": None,
                    "source_mention_id": None,
                    "observed_at": None,
                    "detail": None,
                },
                {
                    "fact_type": "STATE_TRANSITION",
                    "value": "IN_PROGRESS → BLOCKED",
                    "source_meeting_id": "meeting_002",
                    "source_mention_id": "mention_def",
                    "observed_at": "2026-08-08T10:00:00Z",
                    "detail": "Weekly Sync",
                },
                {
                    "fact_type": "REPEATED_OBSERVATION",
                    "value": "3",
                    "source_meeting_id": "meeting_003",
                    "source_mention_id": None,
                    "observed_at": "2026-08-15T10:00:00Z",
                    "detail": "Retrospective",
                },
            ]
        }
    }


# ---------------------------------------------------------------------------
# Memory response schema
# ---------------------------------------------------------------------------

class EntityMemoryResponse(BaseModel):
    """Response body for GET /api/v1/entities/{entity_id}/memory.

    Returns the organisational memory of a canonical entity: structured,
    evidence-backed facts derived from its complete observation history.

    Always returns HTTP 200 when the entity exists.
    Returns HTTP 404 if the entity_id does not exist.

    When the entity exists but has no resolved mentions:
      - first_observed_at and last_observed_at are null.
      - meeting_count and observation_count are 0.
      - current_state is 'UNKNOWN'.
      - facts contains a single CURRENT_STATE fact with value='UNKNOWN'.

    This endpoint answers:
      'What does the organisation currently know about this entity
       based on all available evidence?'

    Read-only invariant: this endpoint never creates or modifies entities,
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
    first_observed_at: Optional[datetime] = Field(
        default=None,
        description=(
            "The meeting_date of the earliest resolved observation.  "
            "Null when no resolved mentions exist."
        ),
    )
    last_observed_at: Optional[datetime] = Field(
        default=None,
        description=(
            "The meeting_date of the most recent resolved observation.  "
            "Null when no resolved mentions exist."
        ),
    )
    meeting_count: int = Field(
        ...,
        ge=0,
        description=(
            "Number of distinct meetings where this entity has been observed.  "
            "0 when no resolved mentions exist."
        ),
    )
    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Total number of resolved observations across all meetings.  "
            "May exceed meeting_count when entity appears multiple times in a meeting."
        ),
    )
    current_state: TemporalStateSchema = Field(
        ...,
        description=(
            "The current lifecycle state of this entity as determined by the "
            "Temporal State Engine.  UNKNOWN when no state-bearing evidence exists."
        ),
    )
    facts: list[EntityMemoryFactSchema] = Field(
        default_factory=list,
        description=(
            "Ordered list of evidence-backed facts about this entity.  "
            "Always contains at least CURRENT_STATE.  "
            "Order: FIRST_OBSERVED, LAST_OBSERVED, CURRENT_STATE, "
            "STATE_TRANSITION (chronological), REPEATED_OBSERVATION (by meeting_date)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "entity_id": "entity_001",
                    "canonical_name": "payment api instability",
                    "entity_type": "ISSUE",
                    "first_observed_at": "2026-08-01T10:00:00Z",
                    "last_observed_at": "2026-08-22T10:00:00Z",
                    "meeting_count": 3,
                    "observation_count": 4,
                    "current_state": "BLOCKED",
                    "facts": [
                        {
                            "fact_type": "FIRST_OBSERVED",
                            "value": "2026-08-01T10:00:00+00:00",
                            "source_meeting_id": "meeting_001",
                            "source_mention_id": "mention_abc",
                            "observed_at": "2026-08-01T10:00:00Z",
                            "detail": "Sprint Planning",
                        },
                        {
                            "fact_type": "LAST_OBSERVED",
                            "value": "2026-08-22T10:00:00+00:00",
                            "source_meeting_id": "meeting_003",
                            "source_mention_id": "mention_ghi",
                            "observed_at": "2026-08-22T10:00:00Z",
                            "detail": "Retrospective",
                        },
                        {
                            "fact_type": "CURRENT_STATE",
                            "value": "BLOCKED",
                            "source_meeting_id": None,
                            "source_mention_id": None,
                            "observed_at": None,
                            "detail": None,
                        },
                        {
                            "fact_type": "STATE_TRANSITION",
                            "value": "UNKNOWN → IN_PROGRESS",
                            "source_meeting_id": "meeting_001",
                            "source_mention_id": "mention_abc",
                            "observed_at": "2026-08-01T10:00:00Z",
                            "detail": "Sprint Planning",
                        },
                        {
                            "fact_type": "STATE_TRANSITION",
                            "value": "IN_PROGRESS → BLOCKED",
                            "source_meeting_id": "meeting_002",
                            "source_mention_id": "mention_def",
                            "observed_at": "2026-08-08T10:00:00Z",
                            "detail": "Weekly Sync",
                        },
                    ],
                },
                {
                    "entity_id": "entity_002",
                    "canonical_name": "database timeout",
                    "entity_type": "ISSUE",
                    "first_observed_at": None,
                    "last_observed_at": None,
                    "meeting_count": 0,
                    "observation_count": 0,
                    "current_state": "UNKNOWN",
                    "facts": [
                        {
                            "fact_type": "CURRENT_STATE",
                            "value": "UNKNOWN",
                            "source_meeting_id": None,
                            "source_mention_id": None,
                            "observed_at": None,
                            "detail": None,
                        }
                    ],
                },
            ]
        }
    }
