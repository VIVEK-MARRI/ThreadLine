"""Internal domain models for the Action Recommendation Engine.

These are the authoritative representations of recommended next actions inside
Threadline. They are *not* tied to any API schema or persistence format —
those layers translate to/from these models as needed.

Design notes
------------
- ActionType is a str Enum.
- EntityAction is the atomic unit of a recommendation, strictly derived from
  Insights and Attention scoring.
- action_id is a deterministic identifier derived from a SHA-256 hash of
  (entity_id, action_type).
- deterministic_sort_key is pre-computed to allow stable ordering without
  re-sorting dynamically.

Relationship to other models
-----------------------------
- EntityAction is derived from EntityAttention (from models/attention.py) and
  EntityInsight (from models/insights.py).
- EntityAction is computed on read and never persisted.
- It is a read-only layer and does not mutate entities or memory.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    """The category of recommended action for a canonical entity.

    ESCALATE
        The entity is blocked or has a critical condition requiring escalation.
    
    REQUEST_UPDATE
        The entity is stale and an update from the responsible owner is needed.

    INVESTIGATE
        The entity requires investigation (e.g., an invalid reopen attempt).

    FOLLOW_UP
        The entity has repeated observations without progress.

    REVIEW
        The entity has a significant recent state change worth noting.

    NO_ACTION
        The entity does not require any specific action.
    """

    ESCALATE = "ESCALATE"
    REQUEST_UPDATE = "REQUEST_UPDATE"
    INVESTIGATE = "INVESTIGATE"
    FOLLOW_UP = "FOLLOW_UP"
    REVIEW = "REVIEW"
    NO_ACTION = "NO_ACTION"


class ActionPriority(str, Enum):
    """Priority of the recommended action. Maps directly to AttentionLevel."""
    
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Entity Action — the atomic unit of recommendation
# ---------------------------------------------------------------------------

class EntityAction(BaseModel):
    """A recommended action for a canonical entity based on its insights.

    An EntityAction is the atomic output of the Action Recommendation Engine.
    It is always grounded in evidence produced by the Prioritization & Attention
    Engine and Insight Engine — never invented.

    Fields
    ------
    action_id
        Deterministic identifier. Derived from a SHA-256 hash of
        (entity_id, action_type). Truncated to 16 hex characters for readability.
        Identical across repeated service invocations given the same data.

    entity_id
        The canonical entity this action refers to.

    action_type
        The category of this action (see ActionType).

    priority
        The priority of the action, derived from the entity's AttentionLevel.

    recommended_action
        Short, human-readable instruction of what to do next.

    reason
        Detailed explanation of why this action is recommended, referencing
        the underlying insights or attention.

    related_insight_ids
        Deduplicated, sorted list of insight_ids that triggered this action.

    related_meeting_id
        The ID of the meeting most directly associated with this action (optional).

    created_from_observation_at
        The timestamp of the underlying event that caused this action.

    deterministic_sort_key
        A pre-computed string for stable ordering:
        "priority_order|created_from_observation_at_iso|entity_id|action_type|action_id".
    """

    action_id: str = Field(
        ...,
        description=(
            "Deterministic 16-character hex identifier derived from a SHA-256 "
            "hash of (entity_id, action_type). Identical for the same "
            "entity state across repeated calls."
        ),
    )

    entity_id: str = Field(
        ..., description="ID of the canonical entity this action refers to."
    )

    action_type: ActionType = Field(
        ..., description="The category of this recommended action."
    )

    priority: ActionPriority = Field(
        ..., description="The priority level of this action, mapped from AttentionLevel."
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
        ...,
        description=(
            "Pre-computed sort key used for stable, reproducible ordering."
        ),
    )
