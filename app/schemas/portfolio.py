"""API schemas for the Organisation-Wide Portfolio Intelligence Engine (Stage 14).

These Pydantic schemas define the public JSON contract for the portfolio API endpoint.
They mirror the internal domain models but provide stable external representations.

Design notes
------------
- PortfolioRiskLevelSchema mirrors PortfolioRiskLevel but is independent so the
  two layers can diverge in the future without a breaking API change.
- PortfolioEntitySummarySchema mirrors PortfolioEntitySummary using only API-safe types.
- OrganisationPortfolioResponse wraps the complete portfolio for the GET /portfolio endpoint.
- All optional fields follow the same Optional[...] = None pattern used throughout
  the schemas layer (insights.py, memory.py, temporal.py, attention.py).
- No internal implementation details are exposed unnecessarily.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabulary (API layer copy)
# ---------------------------------------------------------------------------

class PortfolioRiskLevelSchema(str, Enum):
    """Portfolio risk level values returned by the portfolio API.

    Mirrors the domain model's PortfolioRiskLevel enum.

    CRITICAL — entity has CRITICAL attention or a CRITICAL impact signal.
    HIGH     — entity has HIGH attention, is BLOCKED, or has a HIGH impact signal.
    MEDIUM   — entity has MEDIUM attention, a MEDIUM impact signal, or active
               insights and actions.
    LOW      — entity has observations but no stronger risk condition.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Entity summary schema
# ---------------------------------------------------------------------------

class PortfolioEntitySummarySchema(BaseModel):
    """API representation of a single entity's organisation-level risk summary."""

    entity_id: str = Field(
        ..., description="Unique identifier of the canonical entity."
    )

    entity_type: str = Field(
        ..., description="Category of the canonical entity (PERSON, ISSUE, etc.)."
    )

    canonical_name: str = Field(
        ..., description="Preferred, normalised name of the canonical entity."
    )

    risk_level: PortfolioRiskLevelSchema = Field(
        ...,
        description=(
            "Deterministic organisation-level risk classification.  "
            "Derived from attention level, temporal state, and impact signals."
        ),
    )

    attention_level: Optional[str] = Field(
        default=None,
        description=(
            "The attention level from AttentionService (CRITICAL/HIGH/MEDIUM/LOW), "
            "or null when the entity has no actionable attention signals."
        ),
    )

    attention_score: int = Field(
        default=0,
        ge=0,
        description=(
            "Numeric attention score from AttentionService.  "
            "0 when no attention signals exist."
        ),
    )

    impact_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of risk impact associations directed at this entity.  "
            "Based on CO_OCCURS_WITH relationships — does not imply causal dependency."
        ),
    )

    action_count: int = Field(
        default=0,
        ge=0,
        description="Number of recommended actions from ActionRecommendationService.",
    )

    active_insight_count: int = Field(
        default=0,
        ge=0,
        description="Number of active EntityInsight records from InsightService.",
    )

    current_state: str = Field(
        ...,
        description=(
            "Current temporal lifecycle state (UNKNOWN, OPEN, IN_PROGRESS, "
            "BLOCKED, RESOLVED) from TemporalStateService."
        ),
    )

    observation_count: int = Field(
        ...,
        ge=0,
        description="Total number of resolved observations from TemporalStateService.",
    )


# ---------------------------------------------------------------------------
# Portfolio response envelope
# ---------------------------------------------------------------------------

class OrganisationPortfolioResponse(BaseModel):
    """Response for GET /api/v1/portfolio — organisation-wide portfolio snapshot.

    This is a read-only, deterministic snapshot of the organisation's entity
    risk posture, aggregated from all existing ThreadLine intelligence.

    Entities are sorted by:
    risk_level DESC, attention_score DESC, impact_count DESC,
    action_count DESC, entity_id ASC.
    """

    total_entities: int = Field(
        ...,
        ge=0,
        description="Total number of entities included in this portfolio.",
    )

    critical_entities: int = Field(
        ...,
        ge=0,
        description="Number of entities classified at CRITICAL risk.",
    )

    high_risk_entities: int = Field(
        ...,
        ge=0,
        description="Number of entities classified at HIGH risk.",
    )

    medium_risk_entities: int = Field(
        ...,
        ge=0,
        description="Number of entities classified at MEDIUM risk.",
    )

    low_risk_entities: int = Field(
        ...,
        ge=0,
        description="Number of entities classified at LOW risk.",
    )

    entities_with_active_actions: int = Field(
        ...,
        ge=0,
        description="Number of entities with at least one active recommended action.",
    )

    entities_with_impact: int = Field(
        ...,
        ge=0,
        description=(
            "Number of entities with at least one risk impact association directed "
            "at them.  Based on CO_OCCURS_WITH relationships."
        ),
    )

    blocked_entities: int = Field(
        ...,
        ge=0,
        description="Number of entities whose current temporal lifecycle state is BLOCKED.",
    )

    entities: list[PortfolioEntitySummarySchema] = Field(
        default_factory=list,
        description=(
            "Deterministically sorted portfolio entity summaries.  "
            "Ordering: risk_level DESC, attention_score DESC, "
            "impact_count DESC, action_count DESC, entity_id ASC."
        ),
    )

    evaluated_at: datetime = Field(
        ...,
        description="Timestamp at which this portfolio snapshot was computed (UTC).",
    )
