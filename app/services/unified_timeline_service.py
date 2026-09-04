"""Unified Entity Timeline Service layer.

Provides a deterministic, read-only aggregation of the full chronological
story of a canonical entity.

This service answers: 'What is the complete story of this entity?'

The service:
  - Aggregates existing outputs from TemporalStateService, OrganisationalMemoryService,
    InsightService, AttentionService, and ActionRecommendationService.
  - Generates deterministic timeline events.
  - Sorts events chronologically.
  - Deduplicates or omits redundant signals (e.g., redundant memory facts).
  - Strictly read-only: never mutates entities, meetings, mentions, memory, or state.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from app.models.memory import MemoryFactType
from app.models.timeline import (
    TimelineEvent,
    TimelineEventType,
    UnifiedEntityTimeline,
)
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.action_recommendation_service import ActionRecommendationService
from app.services.attention_service import AttentionService
from app.services.entity_service import EntityNotFoundError
from app.services.insight_service import InsightService, DEFAULT_STALE_THRESHOLD_DAYS
from app.services.organisational_memory_service import OrganisationalMemoryService
from app.services.temporal_state_service import TemporalStateService
from app.temporal.state_interpreter import AbstractStateInterpreter
from app.temporal.transition_policy import AbstractTemporalStatePolicy

logger = logging.getLogger(__name__)


def _make_event_id(entity_id: str, event_type: TimelineEventType, source_id: str) -> str:
    """Return a deterministic 16-character event identifier.

    Computes a SHA-256 digest of (entity_id, event_type, source_id).
    """
    raw = f"{entity_id}:{event_type.value}:{source_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class UnifiedTimelineService:
    """Read-only service that produces a unified entity timeline.

    This service calls existing intelligence layers and transforms their outputs
    into a chronologically ordered event stream.

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

        # Compose existing services
        self._temporal_service = TemporalStateService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )
        self._memory_service = OrganisationalMemoryService(
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
        self._attention_service = AttentionService(
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

    def get_unified_timeline(
        self,
        entity_id: str,
        current_time: Optional[datetime] = None,
        stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
    ) -> UnifiedEntityTimeline:
        """Compute the unified timeline for a single canonical entity.

        Parameters
        ----------
        entity_id:
            ID of the canonical entity.
        current_time:
            Reference datetime. Must be timezone-aware.
        stale_threshold_days:
            Days threshold for staleness used by lower layers.

        Returns
        -------
        UnifiedEntityTimeline
            The aggregated chronological story.

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
            "UnifiedTimelineService: assembling timeline for entity %s (%r).",
            entity_id,
            entity.canonical_name,
        )

        events: list[TimelineEvent] = []

        # 1. Fetch temporal state timeline (Observations and State Changes)
        timeline = self._temporal_service.get_entity_timeline(entity_id)
        for obs in timeline.timeline:
            source_id = f"{obs.meeting_id}:{obs.observation_index}"
            if obs.transition_occurred and obs.from_state != obs.to_state:
                event_type = TimelineEventType.STATE_CHANGE
                title = f"State changed to {obs.to_state.value}"
                metadata = {
                    "from_state": obs.from_state.value,
                    "to_state": obs.to_state.value,
                }
            else:
                event_type = TimelineEventType.OBSERVATION
                title = "Entity observed"
                metadata = {
                    "is_valid_transition": obs.is_valid_transition,
                    "interpreted_state": obs.interpreted_state.value if obs.interpreted_state else None,
                    "transition_skipped_reason": obs.transition_skipped_reason,
                }

            events.append(
                TimelineEvent(
                    event_id=_make_event_id(entity_id, event_type, source_id),
                    entity_id=entity_id,
                    event_type=event_type,
                    occurred_at=obs.meeting_date,
                    related_meeting_id=obs.meeting_id,
                    title=title,
                    description=f"Observation in meeting: {obs.evidence_text}",
                    event_metadata=metadata,
                )
            )

        # 2. Fetch organisational memory facts
        memory = self._memory_service.get_entity_memory(entity_id)
        # Redundant fact types to ignore
        redundant_facts = {
            MemoryFactType.FIRST_OBSERVED,
            MemoryFactType.LAST_OBSERVED,
            MemoryFactType.CURRENT_STATE,
            MemoryFactType.STATE_TRANSITION,
        }
        for fact in memory.facts:
            if fact.fact_type in redundant_facts:
                continue

            source_id = f"{fact.fact_type.value}:{fact.source_meeting_id or 'none'}:{fact.source_mention_id or 'none'}"
            event_type = TimelineEventType.MEMORY_FACT
            events.append(
                TimelineEvent(
                    event_id=_make_event_id(entity_id, event_type, source_id),
                    entity_id=entity_id,
                    event_type=event_type,
                    occurred_at=fact.observed_at,
                    related_meeting_id=fact.source_meeting_id,
                    title="Memory Fact Recorded",
                    description=f"{fact.fact_type.value}: {fact.detail}",
                    event_metadata={"fact_type": fact.fact_type.value, "value": fact.value},
                )
            )

        # 3. Fetch Insights
        insights = self._insight_service.get_entity_insights(
            entity_id=entity_id,
            current_time=ref_time,
            stale_threshold_days=stale_threshold_days,
        )
        for insight in insights:
            event_type = TimelineEventType.INSIGHT
            events.append(
                TimelineEvent(
                    event_id=_make_event_id(entity_id, event_type, insight.insight_id),
                    entity_id=entity_id,
                    event_type=event_type,
                    occurred_at=insight.observed_at,
                    related_meeting_id=insight.related_meeting_id,
                    title=f"Insight: {insight.title}",
                    description=insight.description,
                    event_metadata={
                        "insight_type": insight.insight_type.value,
                        "severity": insight.severity.value,
                    },
                )
            )

        # 4. Fetch Actions
        actions = self._action_service.get_entity_actions(
            entity_id=entity_id,
            current_time=ref_time,
            stale_threshold_days=stale_threshold_days,
        )
        for action in actions:
            event_type = TimelineEventType.ACTION
            events.append(
                TimelineEvent(
                    event_id=_make_event_id(entity_id, event_type, action.action_id),
                    entity_id=entity_id,
                    event_type=event_type,
                    occurred_at=action.created_from_observation_at,
                    related_meeting_id=action.related_meeting_id,
                    title=f"Action Recommended: {action.action_type.value}",
                    description=action.recommended_action,
                    event_metadata={
                        "action_type": action.action_type.value,
                        "priority": action.priority.value,
                        "reason": action.reason,
                    },
                )
            )

        # 5. Fetch Attention Snapshot
        attention = self._attention_service.get_entity_attention(
            entity_id=entity_id,
            current_time=ref_time,
            stale_threshold_days=stale_threshold_days,
        )
        if attention:
            event_type = TimelineEventType.ATTENTION
            events.append(
                TimelineEvent(
                    event_id=_make_event_id(entity_id, event_type, attention.attention_id),
                    entity_id=entity_id,
                    event_type=event_type,
                    occurred_at=attention.evaluated_at,
                    related_meeting_id=None,
                    title=f"Attention Required: {attention.attention_level.value}",
                    description=f"Entity currently has {attention.attention_level.value} priority.",
                    event_metadata={
                        "attention_level": attention.attention_level.value,
                        "score": attention.score,
                        "reasons": [r.value for r in attention.reasons],
                    },
                )
            )

        # Deduplicate safety guard (by event_id)
        seen_ids = set()
        unique_events = []
        for event in events:
            if event.event_id not in seen_ids:
                seen_ids.add(event.event_id)
                unique_events.append(event)

        # Sort deterministically:
        # 1. occurred_at ASC
        # 2. event_type (String alphabetical ensures stable type grouping when timestamps tie)
        # 3. event_id ASC
        unique_events.sort(
            key=lambda e: (
                e.occurred_at,
                e.event_type.value,
                e.event_id,
            )
        )

        return UnifiedEntityTimeline(
            entity_id=entity_id,
            first_observed_at=memory.first_observed_at,
            last_observed_at=memory.last_observed_at,
            events=unique_events,
        )
