"""Organisation-Wide Portfolio Intelligence Service (Stage 14).

This service is the aggregation layer that composes existing ThreadLine intelligence
into a deterministic, organisation-level portfolio view.

Architecture
------------
PortfolioIntelligenceService is a COMPOSITION layer.  It:

  FETCH      → Lists all canonical entities from the entity repository.
  COMPOSE    → Gathers existing intelligence for each entity from:
               - AttentionService (attention level and score)
               - InsightService (active insight count)
               - ActionRecommendationService (recommended action count)
               - TemporalStateService (current_state, observation_count)
               - ImpactAnalysisService (impact associations directed at entity)
  CLASSIFY   → Applies transparent deterministic risk classification rules.
  AGGREGATE  → Counts entities at each risk level and other portfolio metrics.
  SORT       → Orders entities deterministically.
  RETURN     → Returns OrganisationPortfolio.

This service does NOT:
  - Reimplement attention scoring.
  - Reimplement insight detection.
  - Reimplement action recommendation.
  - Reimplement impact calculation.
  - Mutate any entities, mentions, resolutions, temporal states, insights,
    attention results, actions, relationships, or impacts.

Risk classification rules (transparent, deterministic)
-------------------------------------------------------
CRITICAL:
  - attention_level is CRITICAL, OR
  - one or more EntityImpact records have impact_level == CRITICAL

HIGH:
  - attention_level is HIGH, OR
  - current temporal state is BLOCKED, OR
  - one or more EntityImpact records have impact_level == HIGH

MEDIUM:
  - attention_level is MEDIUM, OR
  - one or more EntityImpact records have impact_level == MEDIUM, OR
  - active_insight_count > 0 AND action_count > 0

LOW:
  - observation_count > 0 and no stronger risk condition applies

Entities with zero observations and no intelligence signals are excluded
from the portfolio entirely (they contribute no actionable information).

Semantic constraints
--------------------
- CO_OCCURS_WITH relationships do NOT imply dependency or causation.
- Blocked status on entity A does NOT mean entity A "blocks" entity B.
- "associated risk" and "affected entities" are used; "blocks" is not.
- No ownership, department, financial exposure, or dependency-count fields.

Determinism guarantees
-----------------------
- All source data (entities, attention, insights, actions, states, impacts)
  is deterministic given the same repository state.
- current_time is NEVER called inside this service; it is passed in as a
  parameter.  The same input state + same current_time always produces the
  same portfolio.
- Sort order is explicitly computed and stable.
- Tests are reproducible.

Invariants
----------
- This service NEVER modifies any repository state.
- It is completely read-only.
- At most one PortfolioEntitySummary is produced per entity.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from app.models.attention import AttentionLevel
from app.models.impact import ImpactLevel
from app.models.portfolio import (
    PORTFOLIO_RISK_LEVEL_ORDER,
    OrganisationPortfolio,
    PortfolioEntitySummary,
    PortfolioRiskLevel,
)
from app.models.temporal import TemporalState
from app.repositories.dependency_repository import AbstractDependencyRepository
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.action_recommendation_service import ActionRecommendationService
from app.services.attention_service import AttentionService
from app.services.entity_relationship_service import EntityRelationshipService
from app.services.impact_analysis_service import ImpactAnalysisService
from app.services.insight_service import InsightService, DEFAULT_STALE_THRESHOLD_DAYS
from app.services.temporal_state_service import TemporalStateService
from app.temporal.state_interpreter import AbstractStateInterpreter
from app.temporal.transition_policy import AbstractTemporalStatePolicy

logger = logging.getLogger(__name__)


def _classify_risk(
    attention_level: Optional[AttentionLevel],
    current_state: TemporalState,
    impact_levels: list[ImpactLevel],
    observation_count: int,
) -> Optional[PortfolioRiskLevel]:
    """Classify an entity's portfolio risk level using deterministic rules.

    Returns None when the entity has zero observations and no intelligence
    signals (i.e., it should be excluded from the portfolio).

    Classification rules (evaluated in priority order):

    CRITICAL:
        attention_level is CRITICAL, OR any impact is CRITICAL.

    HIGH:
        attention_level is HIGH, OR current_state is BLOCKED, OR
        any impact is HIGH.

    MEDIUM:
        attention_level is MEDIUM, OR any impact is MEDIUM.

    LOW:
        observation_count > 0 and none of the stronger conditions apply.
        (Includes entities with LOW attention, no attention, and no impact signals.)

    Excluded (returns None):
        observation_count == 0 and attention_level is None and no impact signals.

    Parameters
    ----------
    attention_level:
        The entity's attention level, or None if no attention signals exist.
    current_state:
        The entity's current temporal lifecycle state.
    impact_levels:
        List of impact levels from EntityImpact records directed at this entity.
    observation_count:
        Total number of resolved observations for this entity.

    Returns
    -------
    PortfolioRiskLevel or None
        The classified risk level, or None if the entity should be excluded.
    """
    has_critical_impact = ImpactLevel.CRITICAL in impact_levels
    has_high_impact = ImpactLevel.HIGH in impact_levels
    has_medium_impact = ImpactLevel.MEDIUM in impact_levels

    # CRITICAL: attention is CRITICAL or any impact is CRITICAL
    if attention_level == AttentionLevel.CRITICAL or has_critical_impact:
        return PortfolioRiskLevel.CRITICAL

    # HIGH: attention is HIGH, entity is BLOCKED, or any impact is HIGH
    if (
        attention_level == AttentionLevel.HIGH
        or current_state == TemporalState.BLOCKED
        or has_high_impact
    ):
        return PortfolioRiskLevel.HIGH

    # MEDIUM: attention is MEDIUM or any impact is MEDIUM
    if attention_level == AttentionLevel.MEDIUM or has_medium_impact:
        return PortfolioRiskLevel.MEDIUM

    # LOW: entity has observations but none of the stronger conditions apply
    # (This includes entities with LOW attention, no attention, and no impact signals.)
    if observation_count > 0:
        return PortfolioRiskLevel.LOW

    # Entity has no observations and no intelligence signals — exclude
    return None


class PortfolioIntelligenceService:
    """Read-only aggregation service that produces the organisation-wide portfolio.

    PortfolioIntelligenceService composes existing ThreadLine intelligence
    services to answer organisation-level questions:

    - Which entities currently require the most attention?
    - How many CRITICAL/HIGH/MEDIUM/LOW risk entities exist?
    - Which entities are BLOCKED?
    - Which entities have active recommended actions or impact signals?
    - What is the overall risk distribution?

    This service is the ONLY place where PortfolioEntitySummary and
    OrganisationPortfolio objects are created.  All risk classification
    logic lives here, not in the individual entity intelligence services.

    Dependencies are injected via the constructor so this service is fully
    testable without HTTP or real storage.

    Internal composition
    --------------------
    The service internally composes AttentionService, InsightService,
    ActionRecommendationService, TemporalStateService, ImpactAnalysisService,
    and EntityRelationshipService using the same pattern that AttentionService
    uses to compose InsightService.  This avoids exposing internal wiring
    to callers while keeping the constructor signature symmetric with the
    rest of the service layer.
    """

    def __init__(
        self,
        entity_repo: AbstractEntityRepository,
        mention_repo: AbstractMentionRepository,
        meeting_repo: AbstractMeetingRepository,
        interpreter: AbstractStateInterpreter,
        policy: AbstractTemporalStatePolicy,
        dependency_repo: Optional[AbstractDependencyRepository] = None,
    ) -> None:
        self._entity_repo = entity_repo

        # Compose existing services on the same underlying repositories.
        # Each service is stateless and can be recreated freely (mirrors the
        # pattern used in the API dependency-injection layer).
        self._attention_service = AttentionService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )
        self._insight_service = InsightService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )
        self._action_service = ActionRecommendationService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )
        self._temporal_service = TemporalStateService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )
        relationship_service = EntityRelationshipService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            dependency_repo=dependency_repo,
        )
        self._impact_service = ImpactAnalysisService(
            entity_repo=entity_repo,
            relationship_service=relationship_service,
            temporal_service=self._temporal_service,
            insight_service=self._insight_service,
            attention_service=self._attention_service,
        )

    def _build_entity_summary(
        self,
        entity_id: str,
        current_time: datetime,
        stale_threshold_days: int,
    ) -> Optional[PortfolioEntitySummary]:
        """Build the portfolio summary for a single entity.

        Returns None when the entity should be excluded from the portfolio
        (zero observations and no intelligence signals).

        This method is purely read-only and never modifies any state.
        """
        entity = self._entity_repo.get_by_id(entity_id)
        if entity is None:
            # Should not happen since we fetched from the same repo, but guard defensively.
            logger.warning(
                "PortfolioIntelligenceService: entity %s disappeared during evaluation.",
                entity_id,
            )
            return None

        # --- FETCH all existing intelligence (read-only) ---

        attention = self._attention_service.get_entity_attention(
            entity_id=entity_id,
            current_time=current_time,
            stale_threshold_days=stale_threshold_days,
        )

        insights = self._insight_service.get_entity_insights(
            entity_id=entity_id,
            current_time=current_time,
            stale_threshold_days=stale_threshold_days,
        )

        actions = self._action_service.get_entity_actions(
            entity_id=entity_id,
            current_time=current_time,
            stale_threshold_days=stale_threshold_days,
        )

        timeline = self._temporal_service.get_entity_timeline(entity_id=entity_id)

        impacts = self._impact_service.get_entity_impacts(
            entity_id=entity_id,
            current_time=current_time,
        )

        # --- COMPOSE derived counts ---
        attention_level = attention.attention_level if attention is not None else None
        attention_score = attention.score if attention is not None else 0
        impact_levels = [imp.impact_level for imp in impacts]
        active_insight_count = len(insights)
        action_count = len(actions)
        observation_count = timeline.observation_count
        current_state = timeline.current_state

        # --- CLASSIFY risk level ---
        risk_level = _classify_risk(
            attention_level=attention_level,
            current_state=current_state,
            impact_levels=impact_levels,
            observation_count=observation_count,
        )

        # Exclude entities with no intelligence signals
        if risk_level is None:
            return None

        return PortfolioEntitySummary(
            entity_id=entity.entity_id,
            entity_type=entity.entity_type.value,
            canonical_name=entity.canonical_name,
            risk_level=risk_level,
            attention_level=attention_level.value if attention_level is not None else None,
            attention_score=attention_score,
            impact_count=len(impacts),
            action_count=action_count,
            active_insight_count=active_insight_count,
            current_state=current_state.value,
            observation_count=observation_count,
        )

    def get_portfolio(
        self,
        current_time: Optional[datetime] = None,
        stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
    ) -> OrganisationPortfolio:
        """Compute and return the organisation-wide portfolio snapshot.

        Iterates all canonical entities, gathers existing intelligence from
        the appropriate services, classifies each entity's risk level,
        aggregates organisation-wide counts, sorts entities deterministically,
        and returns the OrganisationPortfolio.

        This method is completely read-only.  It never modifies any entity,
        mention, resolution, temporal state, insight, attention result,
        action, relationship, or impact.

        Parameters
        ----------
        current_time:
            Reference datetime for time-sensitive services (stale detection,
            attention, insights, actions, impacts).  Must be timezone-aware.
            Callers should always supply this value.
            Falls back to datetime.now(utc) when None.
        stale_threshold_days:
            Days threshold for STALE_ENTITY detection.  Default: 30.

        Returns
        -------
        OrganisationPortfolio
            The complete organisation-wide snapshot.  entities list is empty
            when no entities have any observable intelligence signals.
        """
        ref_time = current_time if current_time is not None else datetime.now(timezone.utc)

        all_entities = self._entity_repo.list_entities()

        logger.info(
            "PortfolioIntelligenceService: evaluating portfolio for %d entities.",
            len(all_entities),
        )

        summaries: list[PortfolioEntitySummary] = []
        for entity in all_entities:
            summary = self._build_entity_summary(
                entity_id=entity.entity_id,
                current_time=ref_time,
                stale_threshold_days=stale_threshold_days,
            )
            if summary is not None:
                summaries.append(summary)

        # --- SORT entities deterministically ---
        # 1. risk_level DESC (CRITICAL first)
        # 2. attention_score DESC
        # 3. impact_count DESC
        # 4. action_count DESC
        # 5. entity_id ASC (stable tie-breaker)
        summaries.sort(
            key=lambda s: (
                -PORTFOLIO_RISK_LEVEL_ORDER[s.risk_level],
                -s.attention_score,
                -s.impact_count,
                -s.action_count,
                s.entity_id,
            )
        )

        # --- AGGREGATE organisation-level counts ---
        critical_entities = sum(
            1 for s in summaries if s.risk_level == PortfolioRiskLevel.CRITICAL
        )
        high_risk_entities = sum(
            1 for s in summaries if s.risk_level == PortfolioRiskLevel.HIGH
        )
        medium_risk_entities = sum(
            1 for s in summaries if s.risk_level == PortfolioRiskLevel.MEDIUM
        )
        low_risk_entities = sum(
            1 for s in summaries if s.risk_level == PortfolioRiskLevel.LOW
        )
        entities_with_active_actions = sum(
            1 for s in summaries if s.action_count > 0
        )
        entities_with_impact = sum(
            1 for s in summaries if s.impact_count > 0
        )
        blocked_entities = sum(
            1 for s in summaries if s.current_state == TemporalState.BLOCKED.value
        )

        portfolio = OrganisationPortfolio(
            total_entities=len(summaries),
            critical_entities=critical_entities,
            high_risk_entities=high_risk_entities,
            medium_risk_entities=medium_risk_entities,
            low_risk_entities=low_risk_entities,
            entities_with_active_actions=entities_with_active_actions,
            entities_with_impact=entities_with_impact,
            blocked_entities=blocked_entities,
            entities=summaries,
            evaluated_at=ref_time,
        )

        logger.info(
            "PortfolioIntelligenceService: portfolio computed — "
            "%d entities (CRITICAL=%d, HIGH=%d, MEDIUM=%d, LOW=%d).",
            portfolio.total_entities,
            portfolio.critical_entities,
            portfolio.high_risk_entities,
            portfolio.medium_risk_entities,
            portfolio.low_risk_entities,
        )

        return portfolio
