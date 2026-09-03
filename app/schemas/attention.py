"""Pydantic schemas for the Prioritization & Attention Engine API.

These are the *public contract* of the attention endpoints — what clients
receive.  They are deliberately kept separate from the internal domain
models (app/models/attention.py) so the API surface can remain stable
while the internal models evolve.

Design notes
------------
- AttentionLevelSchema mirrors AttentionLevel but is independent so the
  two layers can diverge in the future without a breaking API change.
- AttentionReasonSchema mirrors AttentionReason for the same reason.
- EntityAttentionSchema mirrors EntityAttention but uses only API-safe types.
- AttentionResponse wraps the list of EntityAttentionSchema for the
  GET /attention endpoint, including a top-level entity_count.
- EntityAttentionDetailResponse wraps the optional single-entity result for
  GET /entities/{entity_id}/attention, including a has_attention boolean so
  clients can quickly distinguish "no signals" from a server error.
- All optional fields follow the same Optional[...] = None pattern used
  throughout the schemas layer (insights.py, memory.py, temporal.py).
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies (API layer copies)
# ---------------------------------------------------------------------------

class AttentionLevelSchema(str, Enum):
    """Attention level values returned by the attention API.

    Mirrors the domain model's AttentionLevel enum.

    CRITICAL — score >= 100.  Requires immediate action.
    HIGH     — score >= 50.
    MEDIUM   — score >= 20.
    LOW      — score > 0 but < 20.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AttentionReasonSchema(str, Enum):
    """Attention reason values returned by the attention API.

    Mirrors the domain model's AttentionReason enum.

    ENTITY_BLOCKED       — entity is currently BLOCKED.
    REOPEN_ATTEMPT       — attempt to reopen a RESOLVED entity.
    ENTITY_STALE         — entity not observed for >= 30 days and not RESOLVED.
    RECENT_STATE_CHANGE  — entity recently transitioned state.
    REPEATED_OBSERVATION — entity observed multiple times in one meeting.
    UNKNOWN_STATE        — entity has observations but no determined state.
    """

    ENTITY_BLOCKED = "ENTITY_BLOCKED"
    REOPEN_ATTEMPT = "REOPEN_ATTEMPT"
    ENTITY_STALE = "ENTITY_STALE"
    RECENT_STATE_CHANGE = "RECENT_STATE_CHANGE"
    REPEATED_OBSERVATION = "REPEATED_OBSERVATION"
    UNKNOWN_STATE = "UNKNOWN_STATE"


# ---------------------------------------------------------------------------
# Core attention schema
# ---------------------------------------------------------------------------

class EntityAttentionSchema(BaseModel):
    """API representation of a single entity's attention result.

    Mirrors the domain model's EntityAttention, with independent enum types
    for API stability.
    """

    attention_id: str = Field(
        ...,
        description=(
            "Deterministic 16-character hex identifier for this attention result."
        ),
    )

    entity_id: str = Field(
        ..., description="ID of the canonical entity this attention result refers to."
    )

    attention_level: AttentionLevelSchema = Field(
        ...,
        description=(
            "Priority level: CRITICAL (>=100), HIGH (>=50), MEDIUM (>=20), LOW (>0)."
        ),
    )

    score: int = Field(
        ...,
        ge=1,
        description="Total attention score — sum of scores for each unique reason.",
    )

    reasons: list[AttentionReasonSchema] = Field(
        ...,
        description="Deduplicated, sorted list of reasons contributing to this result.",
    )

    related_insight_ids: list[str] = Field(
        ...,
        description="Deduplicated, sorted list of insight_ids that contributed.",
    )

    evaluated_at: datetime = Field(
        ...,
        description="Timestamp at which this result was computed (UTC).",
    )


# ---------------------------------------------------------------------------
# Response envelopes
# ---------------------------------------------------------------------------

class AttentionResponse(BaseModel):
    """Response for GET /api/v1/attention — prioritised attention across all entities.

    Items are sorted by: attention_level DESC, score DESC, entity_id ASC.
    """

    entity_count: int = Field(
        ...,
        ge=0,
        description="Number of entities with at least one actionable attention signal.",
    )

    items: list[EntityAttentionSchema] = Field(
        ...,
        description=(
            "List of attention results, sorted by priority descending "
            "(CRITICAL first, then HIGH, MEDIUM, LOW)."
        ),
    )


class EntityAttentionDetailResponse(BaseModel):
    """Response for GET /api/v1/entities/{entity_id}/attention.

    Wraps the optional single-entity attention result.  Clients should check
    has_attention before accessing the attention field.
    """

    entity_id: str = Field(
        ..., description="ID of the queried canonical entity."
    )

    has_attention: bool = Field(
        ...,
        description=(
            "True when the entity has at least one actionable attention signal, "
            "false when it has no signals (score == 0 or no insights)."
        ),
    )

    attention: Optional[EntityAttentionSchema] = Field(
        default=None,
        description=(
            "The attention result for this entity, or null when has_attention=false."
        ),
    )
