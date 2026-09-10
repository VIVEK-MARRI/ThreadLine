"""Internal domain models for the Organisation-Wide Portfolio Intelligence Engine (Stage 14).

These models represent the aggregated, organisation-level view of all entity intelligence
already produced by the preceding ThreadLine pipeline stages.

Design notes
------------
- PortfolioRiskLevel is a StrEnum (consistent with AttentionLevel, ImpactLevel, TemporalState
  throughout the project) so Pydantic v2 serialises it as a plain string.
- PortfolioEntitySummary is the atomic summary of one canonical entity's risk posture.
  It contains ONLY information derivable from existing ThreadLine intelligence — never
  fabricated ownership, financial impact, or dependency chains.
- OrganisationPortfolio is the complete organisation-wide snapshot.  It is computed
  on read and never persisted.
- portfolio_risk_level ordering: CRITICAL > HIGH > MEDIUM > LOW.  Numeric weights
  mirror ATTENTION_LEVEL_ORDER in models/attention.py for consistent project-wide semantics.
- evaluated_at is supplied by the caller (service layer receives current_time from the
  API layer) so business logic never calls datetime.now() internally.

Risk classification rules
--------------------------
CRITICAL:
  - attention_level is CRITICAL, OR
  - one or more impact signals carry ImpactLevel.CRITICAL

HIGH:
  - attention_level is HIGH, OR
  - current temporal state is BLOCKED (TemporalState), OR
  - one or more impact signals carry ImpactLevel.HIGH

MEDIUM:
  - attention_level is MEDIUM, OR
  - one or more impact signals carry ImpactLevel.MEDIUM

LOW:
  - observation_count > 0 and no stronger risk condition applies.
  - Includes entities with LOW attention, no attention, and no impact signals.

Entities with zero observations and no intelligence signals are excluded
from the portfolio entirely (they contribute no actionable information).

Semantic constraints (enforced by PortfolioIntelligenceService, documented here)
----------------------------------------------------------------------------------
- CO_OCCURS_WITH relationships do NOT imply dependency.
- No causal claims: "associated risk" and "affected entities" are used, not "blocks".
- No ownership, department, revenue, or financial fields.
- No fabricated dependency counts.

These models are populated exclusively by PortfolioIntelligenceService and must
never be modified by other pipeline stages.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class PortfolioRiskLevel(str, Enum):
    """The organisation-level risk classification for a canonical entity.

    Derived deterministically from existing ThreadLine intelligence signals
    (attention level, temporal state, impact signals).

    CRITICAL — the entity has CRITICAL attention or a CRITICAL impact signal.
               Requires immediate organisational action.

    HIGH     — the entity has HIGH attention, is BLOCKED in its lifecycle,
               or has a HIGH impact signal.

    MEDIUM   — the entity has MEDIUM attention, a MEDIUM impact signal,
               or meaningful active insights and actions requiring attention.

    LOW      — the entity has observations and intelligence but no stronger
               risk condition applies.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


#: Numeric weight of each PortfolioRiskLevel for deterministic sort ordering.
#: Mirrors ATTENTION_LEVEL_ORDER in models/attention.py.
PORTFOLIO_RISK_LEVEL_ORDER: dict[PortfolioRiskLevel, int] = {
    PortfolioRiskLevel.CRITICAL: 4,
    PortfolioRiskLevel.HIGH: 3,
    PortfolioRiskLevel.MEDIUM: 2,
    PortfolioRiskLevel.LOW: 1,
}


# ---------------------------------------------------------------------------
# Portfolio Entity Summary — atomic output for one canonical entity
# ---------------------------------------------------------------------------

class PortfolioEntitySummary(BaseModel):
    """Organisation-level intelligence summary for one canonical entity.

    Every field is derived from existing ThreadLine intelligence services.
    No field is fabricated or inferred beyond what the system already knows.

    Fields
    ------
    entity_id
        The canonical entity this summary refers to.

    entity_type
        The category of the canonical entity (e.g., "PERSON", "ISSUE").

    canonical_name
        The preferred, normalised name for this entity.

    risk_level
        The deterministic organisation-level risk classification.
        Computed from attention_level, current_state, and impact signals.

    attention_level
        The attention level from AttentionService, or None when the entity
        has no actionable attention signals (score == 0).

    attention_score
        The numeric attention score from AttentionService, or 0 when
        no attention signals exist.  Always >= 1 when attention_level is set.

    impact_count
        The number of EntityImpact records directed AT this entity from
        ImpactAnalysisService.  0 when no associated-risk signals exist.

    action_count
        The number of EntityAction recommendations from
        ActionRecommendationService.  0 when no actions are needed.

    active_insight_count
        The number of EntityInsight records from InsightService.
        Includes all insight types (STATE_CHANGED, ISSUE_BLOCKED, etc.).

    current_state
        The current temporal lifecycle state of this entity (UNKNOWN, OPEN,
        IN_PROGRESS, BLOCKED, RESOLVED) from TemporalStateService.

    observation_count
        The total number of resolved observations for this entity
        from TemporalStateService (EntityTimeline.observation_count).
    """

    entity_id: str = Field(
        ..., description="Unique identifier of the canonical entity."
    )

    entity_type: str = Field(
        ..., description="Category of the canonical entity (e.g., PERSON, ISSUE)."
    )

    canonical_name: str = Field(
        ..., description="Preferred, normalised name of the canonical entity."
    )

    risk_level: PortfolioRiskLevel = Field(
        ...,
        description=(
            "Deterministic organisation-level risk classification.  "
            "Derived from attention_level, current_state, and impact signals."
        ),
    )

    attention_level: Optional[str] = Field(
        default=None,
        description=(
            "The attention level assigned by AttentionService (CRITICAL/HIGH/MEDIUM/LOW), "
            "or None when the entity has no actionable signals (score == 0)."
        ),
    )

    attention_score: int = Field(
        default=0,
        ge=0,
        description=(
            "Numeric attention score from AttentionService.  "
            "0 when no attention signals exist.  >= 1 when attention_level is set."
        ),
    )

    impact_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of risk impact associations directed at this entity "
            "from ImpactAnalysisService.  Based on CO_OCCURS_WITH relationships — "
            "does NOT imply causal dependency."
        ),
    )

    action_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of recommended actions from ActionRecommendationService.  "
            "0 when the entity has no actionable signals."
        ),
    )

    active_insight_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of active EntityInsight records from InsightService.  "
            "Includes all insight types."
        ),
    )

    current_state: str = Field(
        ...,
        description=(
            "Current temporal lifecycle state of this entity "
            "(UNKNOWN, OPEN, IN_PROGRESS, BLOCKED, RESOLVED) "
            "from TemporalStateService."
        ),
    )

    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Total number of resolved observations for this entity "
            "from TemporalStateService."
        ),
    )


# ---------------------------------------------------------------------------
# Organisation Portfolio — the complete org-wide snapshot
# ---------------------------------------------------------------------------

class OrganisationPortfolio(BaseModel):
    """A deterministic, read-only snapshot of the organisation's entity risk posture.

    OrganisationPortfolio aggregates the intelligence already produced for all
    individual entities into a single organisation-level view.  It answers
    questions such as:

    - Which entities currently require the most attention?
    - How many CRITICAL/HIGH/MEDIUM/LOW risk entities exist?
    - Which entities are BLOCKED?
    - Which entities have active recommended actions?
    - What is the overall risk distribution?

    This portfolio is NEVER persisted.  It is recomputed on read from the
    current repository state.

    Fields
    ------
    total_entities
        Total number of canonical entities included in this portfolio.
        Entities with zero observations and no intelligence signals are
        excluded (they provide no actionable information).

    critical_entities
        Count of entities classified at PortfolioRiskLevel.CRITICAL.

    high_risk_entities
        Count of entities classified at PortfolioRiskLevel.HIGH.

    medium_risk_entities
        Count of entities classified at PortfolioRiskLevel.MEDIUM.

    low_risk_entities
        Count of entities classified at PortfolioRiskLevel.LOW.

    entities_with_active_actions
        Count of entities that have at least one active recommended action.

    entities_with_impact
        Count of entities that have at least one risk impact association
        directed at them.

    blocked_entities
        Count of entities whose current temporal lifecycle state is BLOCKED.

    entities
        Deterministically sorted list of PortfolioEntitySummary records.
        Ordering: risk_level DESC, attention_score DESC, impact_count DESC,
        action_count DESC, entity_id ASC.

    evaluated_at
        The timestamp at which this portfolio was computed.
        Always timezone-aware (UTC).  Provided by the caller.
    """

    total_entities: int = Field(
        ...,
        ge=0,
        description="Total number of entities included in the portfolio.",
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
            "Number of entities with at least one risk impact association "
            "directed at them.  Based on CO_OCCURS_WITH relationships."
        ),
    )

    blocked_entities: int = Field(
        ...,
        ge=0,
        description="Number of entities whose current temporal state is BLOCKED.",
    )

    entities: list[PortfolioEntitySummary] = Field(
        default_factory=list,
        description=(
            "Deterministically sorted entity summaries.  "
            "Ordering: risk_level DESC, attention_score DESC, "
            "impact_count DESC, action_count DESC, entity_id ASC."
        ),
    )

    evaluated_at: datetime = Field(
        ...,
        description=(
            "Timestamp at which this portfolio was computed.  "
            "Always timezone-aware (UTC).  Provided by the caller."
        ),
    )
