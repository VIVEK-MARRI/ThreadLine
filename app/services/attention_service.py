"""Prioritization & Attention Engine service layer.

Orchestrates the computation of prioritised attention items for all entities
(or a single entity) by reasoning over existing Insight & Change Detection
Engine output.

The service:
  - Knows about domain models and InsightService.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Is the ONLY place where attention scores are computed.
  - Is completely read-only: it never modifies entities, mentions, meetings,
    correlations, temporal timelines, memory objects, or insights.
  - Is deterministic: the same repository state and same evaluated_at always
    produce the same attention results in the same order.

Attention pipeline (for a single entity)
-----------------------------------------
1. Fetch all insights for the entity via InsightService.
2. Map each InsightType → AttentionReason (INSIGHT_TYPE_TO_REASON table).
3. Deduplicate reasons: each AttentionReason is counted at most once.
4. Sum scores: total = sum(REASON_SCORES[r] for r in unique_reasons).
5. If score == 0: return None (no EntityAttention for this entity).
6. Compute AttentionLevel from total score (compute_attention_level).
7. Collect related_insight_ids from all contributing insights.
8. Compute deterministic attention_id (SHA-256 hash).
9. Return EntityAttention.

Attention pipeline (all entities)
-----------------------------------
1. List all entities from the entity repository.
2. Apply the single-entity pipeline to each entity.
3. Collect non-None results.
4. Sort deterministically:
   (attention_level_order DESC, score DESC, entity_id ASC, attention_id ASC).
5. Return the ordered list.

Determinism guarantees
-----------------------
- attention_id is a deterministic SHA-256 hash of
  (entity_id + '|' + ':'.join(sorted(contributing_insight_ids))).
- All source data (insights) is deterministic given the same repo state.
- Final sort order is explicitly computed and stable.
- datetime.now() is NEVER called inside this service. evaluated_at is
  always passed in as a parameter.

Invariants
----------
- This service NEVER modifies a mention's resolution_status or entity_id.
- This service NEVER creates or modifies a canonical entity.
- This service NEVER triggers candidate generation, scoring, or resolution.
- This service NEVER triggers re-extraction.
- Attention computation is strictly read-only.
- At most one EntityAttention is produced per entity.

Raised exceptions:
  - EntityNotFoundError → raised by get_entity_attention() when entity_id
    is unknown (propagated from InsightService).
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from app.models.attention import (
    ATTENTION_LEVEL_ORDER,
    INSIGHT_TYPE_TO_REASON,
    REASON_SCORES,
    AttentionLevel,
    AttentionReason,
    EntityAttention,
    compute_attention_level,
)
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.entity_service import EntityNotFoundError
from app.services.insight_service import InsightService, DEFAULT_STALE_THRESHOLD_DAYS
from app.temporal.state_interpreter import AbstractStateInterpreter
from app.temporal.transition_policy import AbstractTemporalStatePolicy

logger = logging.getLogger(__name__)


def _make_attention_id(entity_id: str, insight_ids: list[str]) -> str:
    """Return a deterministic 16-character attention identifier.

    Computes a SHA-256 digest of entity_id joined with sorted insight IDs
    and returns the first 16 hexadecimal characters.

    Parameters
    ----------
    entity_id:
        ID of the canonical entity.
    insight_ids:
        List of contributing insight_ids.  They are sorted before hashing
        so insertion order does not affect the result.

    Returns
    -------
    str
        A 16-character lowercase hexadecimal string.
    """
    sorted_ids = ":".join(sorted(insight_ids))
    raw = f"{entity_id}|{sorted_ids}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class AttentionService:
    """Read-only service that computes prioritised attention results.

    AttentionService reasons over existing InsightService output to produce
    deterministic, aggregated attention scores and levels for canonical entities.

    This service answers: 'What should the organisation pay attention to first?'

    Dependencies are injected via the constructor so this service is fully
    testable without HTTP or real storage.

    Internal composition
    --------------------
    InsightService is composed internally using the same pattern that
    InsightService uses to compose OrganisationalMemoryService.  This avoids
    exposing internal wiring to callers while keeping the constructor signature
    symmetric with the rest of the service layer.
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
        # InsightService internally composes OrganisationalMemoryService and
        # TemporalStateService.  We compose only once here.
        self._insight_service = InsightService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_entity_attention(
        self,
        entity_id: str,
        current_time: datetime,
        stale_threshold_days: int,
    ) -> Optional[EntityAttention]:
        """Compute attention for a single entity.

        Parameters
        ----------
        entity_id:
            ID of the entity to evaluate.
        current_time:
            Reference time for stale entity detection.
        stale_threshold_days:
            Days threshold for staleness.

        Returns
        -------
        EntityAttention or None
            None when the entity has no actionable signals (score == 0).

        Raises
        ------
        EntityNotFoundError
            If entity_id does not exist in the repository.
        """
        insights = self._insight_service.get_entity_insights(
            entity_id=entity_id,
            current_time=current_time,
            stale_threshold_days=stale_threshold_days,
        )

        if not insights:
            return None

        # Collect unique reasons and their contributing insight_ids.
        # Each reason is counted AT MOST ONCE per entity (Rule F).
        seen_reasons: set[AttentionReason] = set()
        contributing_insight_ids: list[str] = []

        for insight in insights:
            reason = INSIGHT_TYPE_TO_REASON.get(insight.insight_type)
            if reason is None:
                # e.g., ISSUE_RESOLVED → no attention signal
                continue
            # Always collect the insight_id regardless of deduplication,
            # so the full evidence trail is preserved.
            contributing_insight_ids.append(insight.insight_id)
            seen_reasons.add(reason)

        if not seen_reasons:
            # All insights were ISSUE_RESOLVED (or unmapped) → no attention.
            return None

        # Compute score: sum once per reason (deduplication already done above).
        score = sum(REASON_SCORES[r] for r in seen_reasons)
        if score <= 0:
            return None

        # Build deterministic sorted outputs.
        reasons_sorted: list[AttentionReason] = sorted(
            seen_reasons, key=lambda r: r.value
        )
        insight_ids_sorted = sorted(set(contributing_insight_ids))

        attention_level = compute_attention_level(score)
        attention_id = _make_attention_id(entity_id, insight_ids_sorted)

        return EntityAttention(
            attention_id=attention_id,
            entity_id=entity_id,
            attention_level=attention_level,
            score=score,
            reasons=reasons_sorted,
            related_insight_ids=insight_ids_sorted,
            evaluated_at=current_time,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_entity_attention(
        self,
        entity_id: str,
        current_time: Optional[datetime] = None,
        stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
    ) -> Optional[EntityAttention]:
        """Compute attention for a single entity.

        Parameters
        ----------
        entity_id:
            ID of the canonical entity.
        current_time:
            Reference datetime for stale entity detection.  Must be
            timezone-aware.  Callers should always supply this value.
            Falls back to datetime.now(utc) when None.
        stale_threshold_days:
            Days threshold for STALE_ENTITY detection.  Default: 30.

        Returns
        -------
        EntityAttention or None
            An EntityAttention object when actionable signals exist,
            or None when the entity has no signals (score == 0).

        Raises
        ------
        EntityNotFoundError
            If entity_id does not exist in the repository.
        """
        ref_time = current_time if current_time is not None else datetime.now(timezone.utc)

        # Verify entity exists (propagate EntityNotFoundError to caller).
        entity = self._entity_repo.get_by_id(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        logger.info(
            "AttentionService: computing attention for entity %s (%r).",
            entity_id,
            entity.canonical_name,
        )

        result = self._compute_entity_attention(
            entity_id=entity_id,
            current_time=ref_time,
            stale_threshold_days=stale_threshold_days,
        )

        logger.info(
            "AttentionService: entity %s — attention_level=%s, score=%s.",
            entity_id,
            result.attention_level.value if result else "NONE",
            result.score if result else 0,
        )

        return result

    def get_attention(
        self,
        current_time: Optional[datetime] = None,
        stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
    ) -> list[EntityAttention]:
        """Compute prioritised attention items across ALL entities.

        Iterates all entities in the repository, computes attention for each,
        filters out entities with no actionable signals, and returns the results
        sorted deterministically.

        Ordering: (attention_level DESC, score DESC, entity_id ASC, attention_id ASC)
        CRITICAL entities appear first; ties are broken by score then entity_id.

        Parameters
        ----------
        current_time:
            Reference datetime for stale entity detection.  Must be
            timezone-aware.  Callers should always supply this value.
            Falls back to datetime.now(utc) when None.
        stale_threshold_days:
            Days threshold for STALE_ENTITY detection.  Default: 30.

        Returns
        -------
        list[EntityAttention]
            Sorted list of attention results.  Empty when no entity requires
            attention.
        """
        ref_time = current_time if current_time is not None else datetime.now(timezone.utc)

        all_entities = self._entity_repo.list_entities()

        logger.info(
            "AttentionService: evaluating attention for %d entities.",
            len(all_entities),
        )

        results: list[EntityAttention] = []
        for entity in all_entities:
            try:
                attention = self._compute_entity_attention(
                    entity_id=entity.entity_id,
                    current_time=ref_time,
                    stale_threshold_days=stale_threshold_days,
                )
            except EntityNotFoundError:
                # Should not happen since we fetched from the same repo,
                # but guard defensively.
                logger.warning(
                    "AttentionService: entity %s disappeared during evaluation.",
                    entity.entity_id,
                )
                continue
            if attention is not None:
                results.append(attention)

        # Sort: attention_level DESC (CRITICAL first), score DESC,
        # entity_id ASC, attention_id ASC.
        results.sort(
            key=lambda a: (
                -ATTENTION_LEVEL_ORDER[a.attention_level],
                -a.score,
                a.entity_id,
                a.attention_id,
            )
        )

        logger.info(
            "AttentionService: %d entity(ies) require attention.",
            len(results),
        )

        return results
