"""API response schemas for the Unified Entity Timeline."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Re-exported enums for API schema use
# ---------------------------------------------------------------------------

class TimelineEventTypeSchema(str, Enum):
    """Schema representation of TimelineEventType."""
    OBSERVATION = "OBSERVATION"
    STATE_CHANGE = "STATE_CHANGE"
    MEMORY_FACT = "MEMORY_FACT"
    INSIGHT = "INSIGHT"
    ATTENTION = "ATTENTION"
    ACTION = "ACTION"


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class TimelineEventSchema(BaseModel):
    """API representation of a single timeline event."""

    event_id: str = Field(..., description="Deterministic 16-character hex identifier.")
    entity_id: str = Field(..., description="ID of the canonical entity.")
    event_type: TimelineEventTypeSchema = Field(..., description="The type of this event.")
    occurred_at: datetime = Field(..., description="Timestamp of the event.")
    related_meeting_id: Optional[str] = Field(
        default=None, description="Optional associated meeting ID."
    )
    title: str = Field(..., description="Short summary of the event.")
    description: str = Field(..., description="Detailed explanation of the event.")
    event_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Structured payload specific to the event type."
    )


class UnifiedEntityTimelineResponse(BaseModel):
    """API response for the chronological story of an entity."""

    entity_id: str = Field(..., description="The canonical entity ID.")
    first_observed_at: Optional[datetime] = Field(
        default=None, description="Earliest observation timestamp."
    )
    last_observed_at: Optional[datetime] = Field(
        default=None, description="Most recent observation timestamp."
    )
    event_count: int = Field(..., description="The total number of timeline events.")
    events: list[TimelineEventSchema] = Field(
        default_factory=list, description="Chronological sequence of events."
    )
