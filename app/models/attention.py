"""Internal domain models for the Prioritization & Attention Engine.

These are the authoritative representations of attention/prioritization data
inside Threadline.  They are *not* tied to any API schema or persistence
format — those layers translate to/from these models as needed.

Design notes
------------
- AttentionLevel is a str, Enum (consistent with InsightSeverity, TemporalState,
  InsightType, MemoryFactType throughout the project).
- AttentionReason is a str, Enum for the same reason.
- EntityAttention is the atomic output of the Attention Engine — one prioritised
  attention result for one canonical entity, aggregating all applicable signals.
- attention_id is a deterministic identifier derived from a SHA-256 hash of
  (entity_id + sorted contributing insight IDs) so that running the service
  multiple times always produces the same attention_id for the same entity state.
- score is an integer computed by summing reason-specific scores.  The same
  reason is never counted more than once per entity (deduplication rule).
- evaluated_at is supplied by the caller (API layer provides datetime.now(utc))
  so that business logic never calls datetime.now() internally.

Relationship to other models
-----------------------------
- EntityAttention is derived from EntityInsight (InsightService output).
  It is NOT a superset of EntityInsight.
- InsightType (from models/insights.py) is used in INSIGHT_TYPE_TO_REASON to
  map insight signals to attention reasons.
- EntityAttention is computed on read and never persisted.

Scoring model
--------------
Each AttentionReason carries a fixed integer score:

    ENTITY_BLOCKED      → +100   (maps from ISSUE_BLOCKED)
    REOPEN_ATTEMPT      → +50    (maps from REOPEN_ATTEMPT)
    ENTITY_STALE        → +40    (maps from STALE_ENTITY)
    RECENT_STATE_CHANGE → +20    (maps from STATE_CHANGED)
    REPEATED_OBSERVATION → +15   (maps from REPEATED_OBSERVATION)
    UNKNOWN_STATE       → +10    (maps from UNKNOWN_STATE)

    ISSUE_RESOLVED maps to None → 0 points (entity is done; no action needed).

Attention level thresholds (applied after summing):

    score >= 100  → CRITICAL
    score >= 50   → HIGH
    score >= 20   → MEDIUM
    score > 0     → LOW
    score == 0    → no EntityAttention produced

Invariants
----------
- Each AttentionReason appears at most once per EntityAttention.
- related_insight_ids is a deduplicated, sorted list of insight_ids that
  contributed to the attention score.
- score is always >= 1 when an EntityAttention is emitted.
- evaluated_at is always timezone-aware (UTC).

These models are populated exclusively by AttentionService and must
never be modified by other pipeline stages.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models.insights import InsightType


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class AttentionLevel(str, Enum):
    """The priority level assigned to an entity requiring attention.

    CRITICAL — the entity has at least one blocked or extremely urgent signal
               (score >= 100).  Requires immediate organisational action.

    HIGH     — the entity has high-importance signals such as a reopen attempt
               or a stale blocked status (score >= 50).

    MEDIUM   — the entity has moderate signals such as recent state changes or
               repeated observations (score >= 20).

    LOW      — the entity has minor signals that are worth tracking but do not
               require immediate action (score > 0 but < 20).
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AttentionReason(str, Enum):
    """The specific reason why an entity requires organisational attention.

    Each reason maps to a fixed integer contribution to the attention score.

    ENTITY_BLOCKED
        The entity is currently in the BLOCKED lifecycle state.
        Derived from an ISSUE_BLOCKED insight.
        Score contribution: +100.

    REOPEN_ATTEMPT
        An observation attempted to reopen a RESOLVED entity, but the
        temporal state engine rejected the transition.
        Derived from a REOPEN_ATTEMPT insight.
        Score contribution: +50.

    ENTITY_STALE
        The entity has not been observed for >= 30 days and is not RESOLVED.
        Derived from a STALE_ENTITY insight.
        Score contribution: +40.

    RECENT_STATE_CHANGE
        The entity recently underwent a valid lifecycle state transition.
        Derived from STATE_CHANGED insights.
        Score contribution: +20.

    REPEATED_OBSERVATION
        The entity was mentioned multiple times in a single meeting without
        meaningful progress (a potential productivity bottleneck).
        Derived from REPEATED_OBSERVATION insights.
        Score contribution: +15.

    UNKNOWN_STATE
        The entity has observations but no lifecycle state has been
        determined (no state-bearing keywords found).
        Derived from UNKNOWN_STATE insights.
        Score contribution: +10.
    """

    ENTITY_BLOCKED = "ENTITY_BLOCKED"
    REOPEN_ATTEMPT = "REOPEN_ATTEMPT"
    ENTITY_STALE = "ENTITY_STALE"
    RECENT_STATE_CHANGE = "RECENT_STATE_CHANGE"
    REPEATED_OBSERVATION = "REPEATED_OBSERVATION"
    UNKNOWN_STATE = "UNKNOWN_STATE"


# ---------------------------------------------------------------------------
# Deterministic scoring constants
# ---------------------------------------------------------------------------

#: Maps each AttentionReason to its fixed integer score contribution.
#: Scores are designed so that blocked entities always reach CRITICAL (>=100),
#: reopen attempts reach HIGH (>=50), stale entities reach HIGH if alone,
#: and combinations always produce the expected level.
REASON_SCORES: dict[AttentionReason, int] = {
    AttentionReason.ENTITY_BLOCKED: 100,
    AttentionReason.REOPEN_ATTEMPT: 50,
    AttentionReason.ENTITY_STALE: 40,
    AttentionReason.RECENT_STATE_CHANGE: 20,
    AttentionReason.REPEATED_OBSERVATION: 15,
    AttentionReason.UNKNOWN_STATE: 10,
}

#: Maps each InsightType to its corresponding AttentionReason (or None when
#: the insight contributes no attention signal).
#: ISSUE_RESOLVED → None because a resolved entity requires no further action.
INSIGHT_TYPE_TO_REASON: dict[InsightType, Optional[AttentionReason]] = {
    InsightType.ISSUE_BLOCKED: AttentionReason.ENTITY_BLOCKED,
    InsightType.REOPEN_ATTEMPT: AttentionReason.REOPEN_ATTEMPT,
    InsightType.STALE_ENTITY: AttentionReason.ENTITY_STALE,
    InsightType.STATE_CHANGED: AttentionReason.RECENT_STATE_CHANGE,
    InsightType.REPEATED_OBSERVATION: AttentionReason.REPEATED_OBSERVATION,
    InsightType.UNKNOWN_STATE: AttentionReason.UNKNOWN_STATE,
    InsightType.ISSUE_RESOLVED: None,  # resolved → no action needed
}

#: Numeric weight of each AttentionLevel for deterministic sort ordering.
#: Used in sort keys: CRITICAL > HIGH > MEDIUM > LOW.
ATTENTION_LEVEL_ORDER: dict[AttentionLevel, int] = {
    AttentionLevel.CRITICAL: 4,
    AttentionLevel.HIGH: 3,
    AttentionLevel.MEDIUM: 2,
    AttentionLevel.LOW: 1,
}


def compute_attention_level(score: int) -> AttentionLevel:
    """Compute the deterministic AttentionLevel for a given integer score.

    Thresholds:
        score >= 100  → CRITICAL
        score >= 50   → HIGH
        score >= 20   → MEDIUM
        score > 0     → LOW

    Parameters
    ----------
    score:
        Non-negative integer attention score.

    Returns
    -------
    AttentionLevel

    Raises
    ------
    ValueError
        If score is 0 or negative (no attention object should be created).
    """
    if score <= 0:
        raise ValueError(
            f"Cannot compute AttentionLevel for score={score}. "
            "Entities with score <= 0 should not produce an EntityAttention."
        )
    if score >= 100:
        return AttentionLevel.CRITICAL
    if score >= 50:
        return AttentionLevel.HIGH
    if score >= 20:
        return AttentionLevel.MEDIUM
    return AttentionLevel.LOW


# ---------------------------------------------------------------------------
# Entity Attention — atomic output of the Prioritization & Attention Engine
# ---------------------------------------------------------------------------

class EntityAttention(BaseModel):
    """A prioritised attention result for one canonical entity.

    EntityAttention aggregates all applicable attention signals for a single
    entity into one record.  Each entity produces at most one EntityAttention
    object — multiple signals (e.g., BLOCKED + STALE) are combined into one
    score and one level rather than producing separate records.

    Fields
    ------
    attention_id
        Deterministic identifier.  Derived from a SHA-256 hash of
        (entity_id + '|' + ':'.join(sorted(contributing_insight_ids))).
        Truncated to 16 hex characters.
        Identical for the same entity state across repeated service calls.

    entity_id
        The canonical entity this attention result refers to.

    attention_level
        The computed priority level (CRITICAL, HIGH, MEDIUM, or LOW).
        Determined by the total score against fixed thresholds.

    score
        The total integer attention score for this entity.
        Computed by summing REASON_SCORES for each unique AttentionReason.
        Always >= 1 when an EntityAttention is emitted.

    reasons
        Deduplicated, sorted list of AttentionReasons that contributed to
        the score.  Each reason appears at most once regardless of how many
        underlying insights triggered it.

    related_insight_ids
        Deduplicated, sorted list of insight_ids (from EntityInsight) that
        contributed to this attention result.

    evaluated_at
        The timestamp at which this attention result was computed.
        Provided by the caller (API layer uses datetime.now(utc)) so that
        business logic never calls datetime.now() internally.
    """

    attention_id: str = Field(
        ...,
        description=(
            "Deterministic 16-character hex identifier derived from a SHA-256 "
            "hash of (entity_id + sorted contributing insight IDs).  "
            "Identical for the same entity state across repeated calls."
        ),
    )

    entity_id: str = Field(
        ..., description="ID of the canonical entity this attention result refers to."
    )

    attention_level: AttentionLevel = Field(
        ...,
        description=(
            "The computed priority level for this entity.  "
            "Determined by the total score against fixed thresholds: "
            "CRITICAL>=100, HIGH>=50, MEDIUM>=20, LOW>0."
        ),
    )

    score: int = Field(
        ...,
        ge=1,
        description=(
            "Total integer attention score.  "
            "Sum of REASON_SCORES for each unique AttentionReason.  "
            "Always >= 1 when an EntityAttention is emitted."
        ),
    )

    reasons: list[AttentionReason] = Field(
        ...,
        description=(
            "Deduplicated, sorted list of AttentionReasons contributing to "
            "this attention result.  Each reason appears at most once."
        ),
    )

    related_insight_ids: list[str] = Field(
        ...,
        description=(
            "Deduplicated, sorted list of insight_ids that contributed to "
            "this attention result."
        ),
    )

    evaluated_at: datetime = Field(
        ...,
        description=(
            "Timestamp at which this attention result was computed.  "
            "Always timezone-aware (UTC).  Provided by the caller."
        ),
    )
