"""Insight & Change Detection Engine service layer.

Orchestrates the computation of derived insights for a canonical entity
by reasoning over existing Temporal State and Organisational Memory data.

The service:
  - Knows about domain models, OrganisationalMemoryService, and
    TemporalStateService.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Is the ONLY place where insights are computed.
  - Is completely read-only: it never modifies entities, mentions, meetings,
    correlations, temporal timelines, or memory objects.
  - Is deterministic: the same repository state always produces the same
    insights in the same order.

Insight & Change Detection pipeline
--------------------------------------
1. Fetch the entity. Raise EntityNotFoundError if absent.
2. Compute EntityTimeline via TemporalStateService (all state observations).
3. Compute EntityMemory via OrganisationalMemoryService (all memory facts).
4. Apply insight rules in order. Each rule may produce zero or more insights.
   The rules are applied to the same timeline/memory data — they are
   independent and additive (no rule suppresses another).
5. Deduplicate by insight_id (deterministic hash — duplicates are impossible
   in a correctly-implemented single pass, but the dedup guard is kept for
   safety).
6. Sort insights: (observed_at ASC, entity_id ASC, insight_type ASC,
   insight_id ASC).
7. Return the ordered list.

Insight Rules
-------------
R1 — STATE_CHANGED
    For every valid state transition (transition_occurred=True).

R2 — ISSUE_BLOCKED
    When a valid transition target is BLOCKED.
    Generated in addition to (not instead of) R1.

R3 — ISSUE_RESOLVED
    When a valid transition target is RESOLVED.
    Generated in addition to (not instead of) R1.

R4 — REOPEN_ATTEMPT
    When an observation attempts to transition a RESOLVED entity into
    another state (is_valid_transition=False, from_state=RESOLVED).
    The temporal state remains RESOLVED.

R5 — REPEATED_OBSERVATION
    When OrganisationalMemory records a REPEATED_OBSERVATION fact
    (a meeting with >= 2 observations of this entity).

R6 — UNKNOWN_STATE
    When the entity has at least one observation but current_state is
    UNKNOWN (no state-bearing evidence was found in any observation).

R7 — STALE_ENTITY
    When the entity has at least one observation, current_state is not
    RESOLVED, and the most recent observation is older than the
    configured threshold (default 30 days).

Determinism guarantees
-----------------------
- insight_id is a deterministic SHA-256 hash of
  (entity_id, insight_type, related_meeting_id or "", obs_index or "").
- All source data (timeline, memory) is deterministic given the same repo.
- Final sort key is pre-computed and stable.
- datetime.now() is NEVER called inside this service. current_time is
  always passed in as a parameter.

Invariants
----------
- This service NEVER modifies a mention's resolution_status.
- This service NEVER modifies a mention's entity_id.
- This service NEVER creates or modifies a canonical entity.
- This service NEVER triggers candidate generation.
- This service NEVER triggers candidate scoring.
- This service NEVER triggers the resolution decision engine.
- This service NEVER triggers re-extraction.
- This service NEVER inspects raw transcript content.
- Insight computation is strictly read-only.

Raised exceptions:
  - EntityNotFoundError → 404 (raised when entity_id is unknown)
"""

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.models.insights import (
    EntityInsight,
    InsightSeverity,
    InsightType,
    INSIGHT_SEVERITY,
)
from app.models.memory import MemoryFactType
from app.models.temporal import TemporalState
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.entity_service import EntityNotFoundError
from app.services.organisational_memory_service import OrganisationalMemoryService
from app.services.temporal_state_service import TemporalStateService
from app.temporal.state_interpreter import AbstractStateInterpreter
from app.temporal.transition_policy import AbstractTemporalStatePolicy

logger = logging.getLogger(__name__)

#: Default stale detection threshold in days.
DEFAULT_STALE_THRESHOLD_DAYS: int = 30


def _make_insight_id(
    entity_id: str,
    insight_type: InsightType,
    meeting_id: str,
    obs_index: str,
) -> str:
    """Return a deterministic 16-character insight identifier.

    Computes a SHA-256 digest of the colon-joined canonical components and
    returns the first 16 hexadecimal characters.  This is deterministic,
    collision-resistant for practical use, and idempotent across calls.

    Parameters
    ----------
    entity_id:
        ID of the canonical entity.
    insight_type:
        The InsightType of this insight.
    meeting_id:
        The related meeting ID, or an empty string for entity-level insights
        (STALE_ENTITY, UNKNOWN_STATE).
    obs_index:
        The observation index as a string, or an empty string for
        non-observation-level insights.

    Returns
    -------
    str
        A 16-character lowercase hexadecimal string.
    """
    raw = f"{entity_id}:{insight_type.value}:{meeting_id}:{obs_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _make_sort_key(
    observed_at: datetime,
    entity_id: str,
    insight_type: InsightType,
    insight_id: str,
) -> str:
    """Return the pre-computed deterministic sort key for an insight.

    Format: "observed_at_iso|entity_id|insight_type|insight_id"
    """
    return f"{observed_at.isoformat()}|{entity_id}|{insight_type.value}|{insight_id}"


def _build_insight(
    insight_id: str,
    entity_id: str,
    insight_type: InsightType,
    title: str,
    description: str,
    observed_at: datetime,
    evidence: str,
    related_meeting_id: Optional[str] = None,
) -> EntityInsight:
    """Construct an EntityInsight with deterministic sort key and severity."""
    severity = INSIGHT_SEVERITY[insight_type]
    sort_key = _make_sort_key(observed_at, entity_id, insight_type, insight_id)
    return EntityInsight(
        insight_id=insight_id,
        entity_id=entity_id,
        insight_type=insight_type,
        title=title,
        description=description,
        severity=severity,
        observed_at=observed_at,
        related_meeting_id=related_meeting_id,
        evidence=evidence,
        deterministic_sort_key=sort_key,
    )


class InsightService:
    """Read-only service that computes derived insights for a canonical entity.

    InsightService reasons over existing Temporal State and Organisational
    Memory data.  It applies deterministic rules to produce actionable
    insights about state changes, risks, and staleness.

    This service answers: 'What changed, and which changes are important?'

    Dependencies are injected via the constructor so this service is fully
    testable without HTTP or real storage.

    Internal composition
    --------------------
    OrganisationalMemoryService and TemporalStateService are composed
    internally using the same pattern that OrganisationalMemoryService uses
    to compose TemporalStateService.  This avoids exposing internal wiring
    to callers while keeping the constructor signature symmetric.
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
        # OrganisationalMemoryService internally composes TemporalStateService,
        # which itself composes CorrelationService.  We compose only once here.
        self._memory_service = OrganisationalMemoryService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )
        # TemporalStateService is also needed directly for timeline access.
        self._temporal_service = TemporalStateService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )

    def get_entity_insights(
        self,
        entity_id: str,
        current_time: Optional[datetime] = None,
        stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
    ) -> list[EntityInsight]:
        """Compute and return all derived insights for a canonical entity.

        Applies all insight rules to the entity's temporal lifecycle timeline
        and organisational memory, then returns the results in a deterministic
        order.

        Parameters
        ----------
        entity_id:
            ID of the canonical entity to compute insights for.
        current_time:
            The reference datetime used for stale entity detection.
            Must be timezone-aware.  Callers MUST supply this value —
            the API layer provides datetime.now(timezone.utc).
            When None, defaults to datetime.now(timezone.utc) as a
            fallback (but callers should always supply it explicitly
            to guarantee determinism in tests).
        stale_threshold_days:
            The number of days without observation after which an
            unresolved entity is considered stale.
            Default: 30 days.

        Returns
        -------
        list[EntityInsight]
            All derived insights for the entity, sorted by
            (observed_at ASC, entity_id ASC, insight_type ASC, insight_id ASC).
            Empty list when no insights apply.

        Raises
        ------
        EntityNotFoundError
            If no canonical entity with *entity_id* exists in the repository.
        """
        # Step 1: Verify entity exists.
        entity = self._entity_repo.get_by_id(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        logger.info(
            "InsightService: computing insights for entity %s (%r).",
            entity_id,
            entity.canonical_name,
        )

        # Step 2: Retrieve timeline and memory.
        # Both services guarantee deterministic output given the same repository.
        timeline = self._temporal_service.get_entity_timeline(entity_id)
        memory = self._memory_service.get_entity_memory(entity_id)

        observations = timeline.timeline

        logger.info(
            "InsightService: entity %s — %d observation(s), current_state=%s.",
            entity_id,
            len(observations),
            timeline.current_state.value,
        )

        # Use caller-supplied time; fall back gracefully for non-test contexts.
        ref_time = current_time if current_time is not None else datetime.now(timezone.utc)

        # Collect all insights from all rules.
        insights: list[EntityInsight] = []

        # ----------------------------------------------------------------
        # R1 — STATE_CHANGED
        # R2 — ISSUE_BLOCKED   (additive with R1)
        # R3 — ISSUE_RESOLVED  (additive with R1)
        # R4 — REOPEN_ATTEMPT
        # ----------------------------------------------------------------
        for obs in observations:
            if obs.transition_occurred and obs.from_state != obs.to_state:
                # R1: STATE_CHANGED
                iid = _make_insight_id(
                    entity_id,
                    InsightType.STATE_CHANGED,
                    obs.meeting_id,
                    str(obs.observation_index),
                )
                insights.append(_build_insight(
                    insight_id=iid,
                    entity_id=entity_id,
                    insight_type=InsightType.STATE_CHANGED,
                    title="State changed",
                    description=(
                        f"The entity transitioned from {obs.from_state.value} "
                        f"to {obs.to_state.value}."
                    ),
                    observed_at=obs.meeting_date,
                    evidence=obs.evidence_text,
                    related_meeting_id=obs.meeting_id,
                ))

                # R2: ISSUE_BLOCKED (in addition to R1)
                if obs.to_state == TemporalState.BLOCKED:
                    iid_blocked = _make_insight_id(
                        entity_id,
                        InsightType.ISSUE_BLOCKED,
                        obs.meeting_id,
                        str(obs.observation_index),
                    )
                    insights.append(_build_insight(
                        insight_id=iid_blocked,
                        entity_id=entity_id,
                        insight_type=InsightType.ISSUE_BLOCKED,
                        title="Issue became blocked",
                        description=(
                            f"The entity transitioned from {obs.from_state.value} "
                            f"to BLOCKED."
                        ),
                        observed_at=obs.meeting_date,
                        evidence=obs.evidence_text,
                        related_meeting_id=obs.meeting_id,
                    ))

                # R3: ISSUE_RESOLVED (in addition to R1)
                if obs.to_state == TemporalState.RESOLVED:
                    iid_resolved = _make_insight_id(
                        entity_id,
                        InsightType.ISSUE_RESOLVED,
                        obs.meeting_id,
                        str(obs.observation_index),
                    )
                    insights.append(_build_insight(
                        insight_id=iid_resolved,
                        entity_id=entity_id,
                        insight_type=InsightType.ISSUE_RESOLVED,
                        title="Issue resolved",
                        description=(
                            f"The entity transitioned from {obs.from_state.value} "
                            f"to RESOLVED."
                        ),
                        observed_at=obs.meeting_date,
                        evidence=obs.evidence_text,
                        related_meeting_id=obs.meeting_id,
                    ))

            # R4: REOPEN_ATTEMPT
            # Fired when an observation attempted a transition FROM RESOLVED
            # but the transition was invalid (not applied).
            if (
                not obs.is_valid_transition
                and obs.from_state == TemporalState.RESOLVED
            ):
                iid_reopen = _make_insight_id(
                    entity_id,
                    InsightType.REOPEN_ATTEMPT,
                    obs.meeting_id,
                    str(obs.observation_index),
                )
                insights.append(_build_insight(
                    insight_id=iid_reopen,
                    entity_id=entity_id,
                    insight_type=InsightType.REOPEN_ATTEMPT,
                    title="Reopen attempt detected",
                    description=(
                        f"An observation attempted to transition the entity from "
                        f"RESOLVED to {obs.interpreted_state.value}, but this "
                        f"transition is not permitted.  The entity remains RESOLVED."
                    ),
                    observed_at=obs.meeting_date,
                    evidence=obs.evidence_text,
                    related_meeting_id=obs.meeting_id,
                ))

        # ----------------------------------------------------------------
        # R5 — REPEATED_OBSERVATION
        # Derived from OrganisationalMemory REPEATED_OBSERVATION facts.
        # ----------------------------------------------------------------
        for fact in memory.facts:
            if fact.fact_type == MemoryFactType.REPEATED_OBSERVATION:
                # fact.source_meeting_id and fact.observed_at are always set
                # for REPEATED_OBSERVATION facts (see memory.py invariants).
                iid_rep = _make_insight_id(
                    entity_id,
                    InsightType.REPEATED_OBSERVATION,
                    fact.source_meeting_id or "",
                    "",
                )
                count = int(fact.value)
                insights.append(_build_insight(
                    insight_id=iid_rep,
                    entity_id=entity_id,
                    insight_type=InsightType.REPEATED_OBSERVATION,
                    title="Repeated observation",
                    description=(
                        f"The entity was observed {count} time(s) in meeting "
                        f"'{fact.detail}' without a meaningful state transition."
                    ),
                    observed_at=fact.observed_at,  # type: ignore[arg-type]
                    evidence=(
                        f"Observed {count} time(s) in meeting '{fact.detail}' "
                        f"({fact.source_meeting_id})."
                    ),
                    related_meeting_id=fact.source_meeting_id,
                ))

        # ----------------------------------------------------------------
        # R6 — UNKNOWN_STATE
        # Fired when the entity has observations but no state-bearing evidence
        # was found (current_state remains UNKNOWN after all observations).
        # ----------------------------------------------------------------
        if (
            memory.observation_count > 0
            and timeline.current_state == TemporalState.UNKNOWN
        ):
            iid_unknown = _make_insight_id(
                entity_id,
                InsightType.UNKNOWN_STATE,
                "",
                "",
            )
            # Use last_observed_at as the insight timestamp.
            obs_at = memory.last_observed_at  # always set when obs_count > 0
            insights.append(_build_insight(
                insight_id=iid_unknown,
                entity_id=entity_id,
                insight_type=InsightType.UNKNOWN_STATE,
                title="Unknown state",
                description=(
                    f"The entity has {memory.observation_count} observation(s) "
                    f"but no meaningful lifecycle state has been determined.  "
                    f"No state-bearing keywords were found in any observation."
                ),
                observed_at=obs_at,  # type: ignore[arg-type]
                evidence=(
                    f"Entity has {memory.observation_count} observation(s) across "
                    f"{memory.meeting_count} meeting(s) but current state is UNKNOWN."
                ),
                related_meeting_id=None,
            ))

        # ----------------------------------------------------------------
        # R7 — STALE_ENTITY
        # Fired when the entity has observations, is not RESOLVED, and the
        # most recent observation is older than the stale threshold.
        # ----------------------------------------------------------------
        if (
            memory.observation_count > 0
            and timeline.current_state != TemporalState.RESOLVED
            and memory.last_observed_at is not None
        ):
            threshold = timedelta(days=stale_threshold_days)
            # Ensure both datetimes are timezone-aware for comparison.
            last_seen = memory.last_observed_at
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)

            age = ref_time - last_seen
            if age > threshold:
                iid_stale = _make_insight_id(
                    entity_id,
                    InsightType.STALE_ENTITY,
                    "",
                    "",
                )
                days_stale = age.days
                insights.append(_build_insight(
                    insight_id=iid_stale,
                    entity_id=entity_id,
                    insight_type=InsightType.STALE_ENTITY,
                    title="Stale entity",
                    description=(
                        f"Entity '{entity.canonical_name}' has not been observed "
                        f"for {days_stale} day(s) and is not RESOLVED.  "
                        f"Current state: {timeline.current_state.value}."
                    ),
                    observed_at=memory.last_observed_at,
                    evidence=(
                        f"Entity last observed at "
                        f"{memory.last_observed_at.isoformat()}, "
                        f"{days_stale} day(s) ago."
                    ),
                    related_meeting_id=None,
                ))

        # ----------------------------------------------------------------
        # Step 5: Deduplicate by insight_id (safety guard).
        # In a correct single-pass implementation duplicates are impossible,
        # but we guard against future bugs.
        # ----------------------------------------------------------------
        seen_ids: set[str] = set()
        unique_insights: list[EntityInsight] = []
        for insight in insights:
            if insight.insight_id not in seen_ids:
                seen_ids.add(insight.insight_id)
                unique_insights.append(insight)

        # ----------------------------------------------------------------
        # Step 6: Sort deterministically.
        # Order: (observed_at ASC, entity_id ASC, insight_type ASC, insight_id ASC)
        # ----------------------------------------------------------------
        unique_insights.sort(
            key=lambda i: (
                i.observed_at,
                i.entity_id,
                i.insight_type.value,
                i.insight_id,
            )
        )

        logger.info(
            "InsightService: entity %s — %d insight(s) produced.",
            entity_id,
            len(unique_insights),
        )

        return unique_insights
