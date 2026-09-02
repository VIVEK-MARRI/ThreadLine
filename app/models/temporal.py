"""Internal domain models for the Temporal State Engine.

These are the authoritative representations of temporal lifecycle state inside
Threadline.  They are *not* tied to any API schema or persistence format —
those layers translate to/from these models as needed.

Design notes
------------
- TemporalState is a StrEnum (like EntityType and ResolutionStatus) so Pydantic
  v2 serialises it as a plain string without extra model config.
- StateObservation is a lightweight read-model that enriches EntityObservation
  (from the correlation layer) with a temporal interpretation.
- EntityTimeline is the top-level aggregation result for one canonical entity.
  It is computed on read and never persisted.
- TemporalState.RESOLVED is NOT the same as ResolutionStatus.RESOLVED.
  ResolutionStatus.RESOLVED means a mention was matched to a canonical entity.
  TemporalState.RESOLVED means the tracked work item (e.g., an ISSUE) is done.
  They are completely separate concerns in separate models.

These models are populated exclusively by TemporalStateService and must
never be modified by other pipeline stages.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models.entity import EntityType


# ---------------------------------------------------------------------------
# Controlled vocabulary
# ---------------------------------------------------------------------------

class TemporalState(str, Enum):
    """The interpreted lifecycle state of a tracked subject at a point in time.

    These states describe the *work item state* of an entity (e.g., an ISSUE)
    as inferred from chronological meeting observations.

    UNKNOWN     — no state-bearing evidence has been found yet.  This is the
                  initial state for all entities.
    OPEN        — the subject has been raised or identified but work has not
                  yet started.
    IN_PROGRESS — the subject is actively being worked on.
    BLOCKED     — work was in progress but is now blocked, stalled, or waiting.
    RESOLVED    — the subject has been completed, fixed, or closed.

    Important: RESOLVED here refers to a work-item being done — it is completely
    separate from ResolutionStatus.RESOLVED (entity-mention matching).
    """

    UNKNOWN = "UNKNOWN"
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    RESOLVED = "RESOLVED"


# ---------------------------------------------------------------------------
# State Observation — one interpreted observation in the timeline
# ---------------------------------------------------------------------------

class StateObservation(BaseModel):
    """A single observation in the temporal lifecycle of a canonical entity.

    A StateObservation enriches one EntityObservation (from the correlation
    layer) with:
      - the state inferred from its evidence text
      - whether a state transition occurred at this point
      - transition validity metadata

    This is the atomic unit of an EntityTimeline.

    Example (transition observed)
    ------------------------------
    observation_index: 0
    meeting_id:        "meeting_001"
    meeting_title:     "Sprint Planning"
    meeting_date:      2026-08-21T10:00:00Z
    mention_id:        "mention_abc"
    evidence_text:     "The payment API has started being investigated."
    interpreted_state: IN_PROGRESS
    transition_occurred:       True
    from_state:        UNKNOWN
    to_state:          IN_PROGRESS
    is_valid_transition:       True
    transition_skipped_reason: None

    Example (invalid transition recorded but not applied)
    ------------------------------------------------------
    interpreted_state: IN_PROGRESS
    transition_occurred:       False
    from_state:        RESOLVED
    to_state:          RESOLVED      (current_state unchanged)
    is_valid_transition:       False
    transition_skipped_reason: "Transition RESOLVED -> IN_PROGRESS is not permitted."
    """

    # Position in the timeline
    observation_index: int = Field(
        ...,
        ge=0,
        description=(
            "Zero-based position of this observation in the chronological timeline."
        ),
    )

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

    # Mention provenance
    mention_id: str = Field(
        ..., description="ID of the resolved EntityMention that produced this observation."
    )
    evidence_text: str = Field(
        ...,
        description=(
            "The surrounding transcript excerpt (source_text of the EntityMention) "
            "used to interpret the state."
        ),
    )

    # State interpretation result
    interpreted_state: TemporalState = Field(
        ...,
        description=(
            "The lifecycle state inferred from evidence_text by the state interpreter.  "
            "UNKNOWN when no state-bearing keywords were found."
        ),
    )

    # Transition metadata
    transition_occurred: bool = Field(
        ...,
        description=(
            "True when this observation caused a valid state change.  "
            "False for repeated states or invalid transitions."
        ),
    )
    from_state: TemporalState = Field(
        ...,
        description="The state immediately before this observation was processed.",
    )
    to_state: TemporalState = Field(
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
            "permitted by the transition policy.  "
            "False for invalid transitions (e.g., RESOLVED -> IN_PROGRESS)."
        ),
    )
    transition_skipped_reason: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable explanation of why the transition was not applied.  "
            "None when is_valid_transition is True."
        ),
    )


# ---------------------------------------------------------------------------
# Entity Timeline — the complete temporal history of one canonical entity
# ---------------------------------------------------------------------------

class EntityTimeline(BaseModel):
    """The complete temporal lifecycle history of a single canonical entity.

    EntityTimeline aggregates all resolved observations of a canonical entity
    across all meetings, interprets each observation's state, and records
    the resulting lifecycle transitions.

    It is the output of TemporalStateService.get_entity_timeline() and is
    computed on read — never persisted.

    Ordering: observations are sorted by
      (meeting_date ASC, meeting_id ASC, mention_id ASC).
    This is identical to the ordering used by CorrelationService.

    Only RESOLVED mentions participate.  AMBIGUOUS and UNRESOLVED mentions
    are excluded (they have no confirmed canonical identity).
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

    # Temporal lifecycle summary
    current_state: TemporalState = Field(
        ...,
        description=(
            "The current (most recent) lifecycle state of this entity.  "
            "UNKNOWN when there are no observations or no state-bearing evidence "
            "was found."
        ),
    )

    # Counts
    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Total number of resolved observations for this entity.  "
            "Equals len(timeline)."
        ),
    )
    transition_count: int = Field(
        ...,
        ge=0,
        description=(
            "Number of valid state transitions that occurred.  "
            "Equals the count of timeline entries where transition_occurred is True."
        ),
    )

    # Full chronological timeline
    timeline: list[StateObservation] = Field(
        default_factory=list,
        description=(
            "Chronologically ordered list of all interpreted observations of this "
            "entity across meetings.  Empty when no resolved mentions exist.  "
            "Ordered by (meeting_date ASC, meeting_id ASC, mention_id ASC)."
        ),
    )
