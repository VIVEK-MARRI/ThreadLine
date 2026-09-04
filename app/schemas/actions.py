"""API response schemas for the Action Recommendation Engine."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.actions import ActionType, ActionPriority


from enum import Enum

# ---------------------------------------------------------------------------
# Re-exported enums for API schema use
# ---------------------------------------------------------------------------

class ActionTypeSchema(str, Enum):
    """Schema representation of ActionType."""
    ESCALATE = "ESCALATE"
    REQUEST_UPDATE = "REQUEST_UPDATE"
    INVESTIGATE = "INVESTIGATE"
    FOLLOW_UP = "FOLLOW_UP"
    REVIEW = "REVIEW"
    NO_ACTION = "NO_ACTION"


class ActionPrioritySchema(str, Enum):
    """Schema representation of ActionPriority."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class EntityActionSchema(BaseModel):
    """API representation of a single recommended action."""

    action_id: str = Field(
        ..., description="Deterministic 16-character hex identifier for this action."
    )
    entity_id: str = Field(..., description="ID of the canonical entity.")
    action_type: ActionTypeSchema = Field(
        ..., description="The category of this recommended action."
    )
    priority: ActionPrioritySchema = Field(
        ..., description="The priority level of this action."
    )
    recommended_action: str = Field(
        ..., description="Short, human-readable instruction of what to do next."
    )
    reason: str = Field(
        ..., description="Explanation of why this action is recommended."
    )
    related_insight_ids: list[str] = Field(
        ..., description="Sorted list of insight IDs that triggered this action."
    )
    related_meeting_id: Optional[str] = Field(
        default=None,
        description="ID of the meeting associated with this action, if any.",
    )
    created_from_observation_at: datetime = Field(
        ..., description="Timestamp of the event causing this recommendation."
    )
    deterministic_sort_key: str = Field(
        ..., description="Pre-computed sort key used for stable ordering."
    )


class EntityActionsResponse(BaseModel):
    """API response for a list of recommended actions for a canonical entity."""

    entity_id: str = Field(..., description="The canonical entity these actions refer to.")
    action_count: int = Field(..., description="The total number of recommended actions.")
    actions: list[EntityActionSchema] = Field(
        ..., description="The recommended actions, ordered deterministically."
    )
