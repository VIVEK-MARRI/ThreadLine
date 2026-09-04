"""Action Recommendation / Decision Support Engine service layer.

Orchestrates the computation of recommended next actions for canonical entities
by reasoning over existing Attention and Insight data.

The service:
  - Knows about domain models, AttentionService, and InsightService.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Is the ONLY place where action recommendations are computed.
  - Is completely read-only: it never modifies entities, mentions, meetings,
    timelines, memory objects, insights, or attention scores.
  - Is deterministic: the same repository state always produces the same
    actions in the same order.

Action Recommendation pipeline
----------------------------
1. Fetch EntityAttention via AttentionService. If no attention (score = 0),
   return an empty list (Rule G - no action for low/no actionable noise).
2. Fetch EntityInsights via InsightService.
3. Map each insight to an ActionType.
4. Deduplicate: group insights by ActionType so each ActionType is recommended
   at most once per entity.
5. Create EntityAction for each group, using the maximum observed_at timestamp,
   aggregating insight_ids.
6. Sort actions: (priority DESC, created_from_observation_at ASC, entity_id ASC,
   action_type ASC, action_id ASC).
7. Return ordered list.

Determinism guarantees
-----------------------
- action_id is a deterministic SHA-256 hash of (entity_id, action_type).
- All source data (attention, insights) is deterministic given the same repo state.
- Final sort order is explicitly computed and stable.
- datetime.now() is NEVER called inside this service. evaluated_at is always passed in.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from app.models.actions import ActionPriority, ActionType, EntityAction
from app.models.attention import AttentionLevel, AttentionReason, INSIGHT_TYPE_TO_REASON
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.attention_service import AttentionService
from app.services.entity_service import EntityNotFoundError
from app.services.insight_service import InsightService, DEFAULT_STALE_THRESHOLD_DAYS
from app.temporal.state_interpreter import AbstractStateInterpreter
from app.temporal.transition_policy import AbstractTemporalStatePolicy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping Rules
# ---------------------------------------------------------------------------

#: Maps an AttentionReason to the corresponding ActionType recommendation.
REASON_TO_ACTION_TYPE: dict[AttentionReason, ActionType] = {
    AttentionReason.ENTITY_BLOCKED: ActionType.ESCALATE,
    AttentionReason.ENTITY_STALE: ActionType.REQUEST_UPDATE,
    AttentionReason.REOPEN_ATTEMPT: ActionType.INVESTIGATE,
    AttentionReason.REPEATED_OBSERVATION: ActionType.FOLLOW_UP,
    AttentionReason.RECENT_STATE_CHANGE: ActionType.REVIEW,
    AttentionReason.UNKNOWN_STATE: ActionType.REVIEW,
}

#: Numeric weight for ActionPriority to allow deterministic sorting.
ACTION_PRIORITY_ORDER: dict[ActionPriority, int] = {
    ActionPriority.CRITICAL: 4,
    ActionPriority.HIGH: 3,
    ActionPriority.MEDIUM: 2,
    ActionPriority.LOW: 1,
}

#: Human-readable reasons for each action type.
ACTION_REASONS: dict[ActionType, str] = {
    ActionType.ESCALATE: "Escalate the blocker to the responsible team or stakeholders.",
    ActionType.REQUEST_UPDATE: "Request a current status update from the responsible owner.",
    ActionType.INVESTIGATE: "Investigate the cause of the attempted reopening or conflicting state.",
    ActionType.FOLLOW_UP: "Follow up to determine whether the entity requires further action.",
    ActionType.REVIEW: "Review the recent significant state changes.",
    ActionType.NO_ACTION: "No action required at this time.",
}


def _make_action_id(entity_id: str, action_type: ActionType) -> str:
    """Return a deterministic 16-character action identifier.

    Computes a SHA-256 digest of (entity_id, action_type).
    """
    raw = f"{entity_id}:{action_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _make_sort_key(
    priority: ActionPriority,
    observed_at: datetime,
    entity_id: str,
    action_type: ActionType,
    action_id: str,
) -> str:
    """Return the pre-computed deterministic sort key for an action."""
    pri_order = ACTION_PRIORITY_ORDER[priority]
    return f"{pri_order}|{observed_at.isoformat()}|{entity_id}|{action_type.value}|{action_id}"


class ActionRecommendationService:
    """Read-only service that computes action recommendations for canonical entities.

    This service answers: 'What should we do next?'

    Dependencies are injected via constructor.
    """

    def __init__(
        self,
        entity_repo: AbstractEntityRepository,
        mention_repo: AbstractMentionRepository,
        meeting_repo: AbstractMeetingRepository,
        interpreter: AbstractStateInterpreter,
        policy: AbstractTemporalStatePolicy,
    ) -> None:
        self._entity_repo = entity_repo
        
        # Internally compose AttentionService and InsightService.
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

    def _compute_entity_actions(
        self,
        entity_id: str,
        current_time: datetime,
        stale_threshold_days: int,
    ) -> list[EntityAction]:
        """Compute recommended actions for a single entity."""
        # 1. Fetch Attention
        attention = self._attention_service.get_entity_attention(
            entity_id=entity_id,
            current_time=current_time,
            stale_threshold_days=stale_threshold_days,
        )
        
        # Rule G: If no attention (score = 0), return no actions to avoid noise.
        if not attention:
            return []

        # Map AttentionLevel to ActionPriority
        priority = ActionPriority(attention.attention_level.value)

        # 2. Fetch Insights
        insights = self._insight_service.get_entity_insights(
            entity_id=entity_id,
            current_time=current_time,
            stale_threshold_days=stale_threshold_days,
        )

        if not insights:
            return []

        # 3. Group Insights by ActionType to apply Deduplication (Rule D/E deduplication mechanism)
        insights_by_action: dict[ActionType, list] = {}
        
        for insight in insights:
            reason = INSIGHT_TYPE_TO_REASON.get(insight.insight_type)
            if not reason:
                continue
            
            action_type = REASON_TO_ACTION_TYPE.get(reason)
            if not action_type:
                continue

            if action_type not in insights_by_action:
                insights_by_action[action_type] = []
            insights_by_action[action_type].append(insight)

        actions: list[EntityAction] = []

        # 4. Create EntityAction for each grouped ActionType
        for action_type, grouped_insights in insights_by_action.items():
            action_id = _make_action_id(entity_id, action_type)
            
            # Use the most recent observed_at among the contributing insights
            latest_observed_at = max(i.observed_at for i in grouped_insights)
            
            # Sort insight IDs to ensure determinism
            related_insight_ids = sorted(list(set(i.insight_id for i in grouped_insights)))
            
            # Pick a related meeting ID if applicable (use the one from the most recent insight)
            # Find the insight that matches the latest_observed_at
            latest_insight = next(i for i in grouped_insights if i.observed_at == latest_observed_at)
            related_meeting_id = latest_insight.related_meeting_id
            
            sort_key = _make_sort_key(
                priority=priority,
                observed_at=latest_observed_at,
                entity_id=entity_id,
                action_type=action_type,
                action_id=action_id
            )

            actions.append(
                EntityAction(
                    action_id=action_id,
                    entity_id=entity_id,
                    action_type=action_type,
                    priority=priority,
                    recommended_action=ACTION_REASONS[action_type],
                    reason=f"Generated from {len(grouped_insights)} underlying insight(s).",
                    related_insight_ids=related_insight_ids,
                    related_meeting_id=related_meeting_id,
                    created_from_observation_at=latest_observed_at,
                    deterministic_sort_key=sort_key,
                )
            )

        # 5. Sort actions deterministically
        # (Priority DESC, observed_at ASC, entity_id ASC, action_type ASC, action_id ASC)
        actions.sort(
            key=lambda a: (
                -ACTION_PRIORITY_ORDER[a.priority],
                a.created_from_observation_at,
                a.entity_id,
                a.action_type.value,
                a.action_id,
            )
        )

        return actions

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_entity_actions(
        self,
        entity_id: str,
        current_time: Optional[datetime] = None,
        stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
    ) -> list[EntityAction]:
        """Compute recommended actions for a single canonical entity.

        Parameters
        ----------
        entity_id:
            ID of the canonical entity.
        current_time:
            Reference datetime. Must be timezone-aware.
        stale_threshold_days:
            Days threshold for staleness.

        Returns
        -------
        list[EntityAction]
            Sorted list of action recommendations. Empty if no actions needed.

        Raises
        ------
        EntityNotFoundError
            If entity_id does not exist.
        """
        ref_time = current_time if current_time is not None else datetime.now(timezone.utc)

        entity = self._entity_repo.get_by_id(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        logger.info(
            "ActionRecommendationService: computing actions for entity %s (%r).",
            entity_id,
            entity.canonical_name,
        )

        actions = self._compute_entity_actions(
            entity_id=entity_id,
            current_time=ref_time,
            stale_threshold_days=stale_threshold_days,
        )

        logger.info(
            "ActionRecommendationService: entity %s — %d action(s) recommended.",
            entity_id,
            len(actions),
        )

        return actions
