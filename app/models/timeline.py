"""Internal domain models for the Unified Entity Timeline.

These models provide a chronological, read-only aggregation of the full story
of a canonical entity across observations, state transitions, memory facts,
insights, attention, and action recommendations.

Design notes
------------
- TimelineEventType classifies the origin of the event.
- TimelineEvent is a single chronological event.
- UnifiedEntityTimeline aggregates these events and snapshot properties.
- All IDs must be deterministic, derived from the underlying data.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class TimelineEventType(str, Enum):
    """The origin or category of the timeline event.

    OBSERVATION
        A raw observation of the entity in a meeting that did not result
        in a state transition.
    
    STATE_CHANGE
        A valid state transition detected by the Temporal State Engine.

    MEMORY_FACT
        A significant historical milestone from the Organisational Memory Engine
        (e.g., repeated observations). Excludes redundant facts.

    INSIGHT
        A derived finding from the Insight & Change Detection Engine.

    ATTENTION
        A snapshot of the entity's current attention priority from the
        Prioritization & Attention Engine.

    ACTION
        A recommended next action from the Action Recommendation Engine.
    """

    OBSERVATION = "OBSERVATION"
    STATE_CHANGE = "STATE_CHANGE"
    MEMORY_FACT = "MEMORY_FACT"
    INSIGHT = "INSIGHT"
    ATTENTION = "ATTENTION"
    ACTION = "ACTION"


# ---------------------------------------------------------------------------
# Timeline Models
# ---------------------------------------------------------------------------

class TimelineEvent(BaseModel):
    """A discrete event in the entity's history.

    Fields
    ------
    event_id
        Deterministic identifier (usually 16 hex chars). Hash of entity_id,
        event_type, and source identifier.
    entity_id
        The canonical entity this event relates to.
    event_type
        The category of this event.
    occurred_at
        The timestamp when this event occurred (or was evaluated for snapshots).
    related_meeting_id
        The ID of the associated meeting, if applicable.
    title
        A short, human-readable summary of the event.
    description
        Detailed text explaining the event.
    metadata
        Structured data specific to the event type (e.g., previous_state, new_state).
    """

    event_id: str = Field(..., description="Deterministic identifier for this event.")
    entity_id: str = Field(..., description="ID of the canonical entity.")
    event_type: TimelineEventType = Field(..., description="The type/origin of this event.")
    occurred_at: datetime = Field(..., description="Timestamp of the event.")
    related_meeting_id: Optional[str] = Field(
        default=None, description="Optional associated meeting ID."
    )
    title: str = Field(..., description="Short summary of the event.")
    description: str = Field(..., description="Detailed explanation of the event.")
    event_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Structured payload specific to the event type."
    )


class UnifiedEntityTimeline(BaseModel):
    """The aggregated chronological story of an entity.

    Fields
    ------
    entity_id
        The canonical entity ID.
    first_observed_at
        The earliest observation timestamp (or None if unobserved).
    last_observed_at
        The most recent observation timestamp (or None if unobserved).
    events
        Deterministically sorted list of timeline events.
    """

    entity_id: str = Field(..., description="ID of the canonical entity.")
    first_observed_at: Optional[datetime] = Field(
        default=None, description="Earliest observation timestamp."
    )
    last_observed_at: Optional[datetime] = Field(
        default=None, description="Most recent observation timestamp."
    )
    events: list[TimelineEvent] = Field(
        default_factory=list, description="Chronological sequence of events."
    )
