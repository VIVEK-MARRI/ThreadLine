"""Organisational Memory service layer.

Orchestrates the computation of organisational memory for a canonical entity.

The service:
  - Knows about domain models, TemporalStateService (which internally uses
    CorrelationService), and the memory read-model.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Is the ONLY place where organisational memory records are computed.

Organisational Memory pipeline
--------------------------------
1. Fetch the canonical entity by ID.  Raise EntityNotFoundError if absent.
2. Compute the entity timeline via TemporalStateService.  The result contains
   chronologically ordered StateObservation records (RESOLVED-only) enriched
   with transition metadata.
3. From the timeline, derive:
   a. first_observed_at  — meeting_date of observations[0]
   b. last_observed_at   — meeting_date of observations[-1]
   c. observation_count  — len(timeline.timeline)
   d. meeting_count      — len({obs.meeting_id for obs in timeline.timeline})
   e. current_state      — timeline.current_state
4. Construct deterministic memory facts (in fixed order):
   a. FIRST_OBSERVED    — if observation_count >= 1
   b. LAST_OBSERVED     — if observation_count >= 2 (first != last by index)
   c. CURRENT_STATE     — always (aggregate, no evidence pointers)
   d. STATE_TRANSITION  — one per valid transition (transition_occurred=True),
                          chronological order.  Invalid transitions excluded.
   e. REPEATED_OBSERVATION — one per meeting with >= 2 observations,
                             ordered by (meeting_date ASC, meeting_id ASC).
5. Return EntityMemory.

Invariants
----------
- This service NEVER modifies a mention's resolution_status.
- This service NEVER modifies a mention's entity_id.
- This service NEVER creates or modifies a canonical entity.
- This service NEVER triggers candidate generation.
- This service NEVER triggers candidate scoring.
- This service NEVER triggers the resolution decision engine.
- This service NEVER triggers re-extraction.
- Organisational memory computation is strictly read-only.
- Only RESOLVED mentions (via TemporalStateService → CorrelationService) participate.
- Output is fully deterministic given the same repository state.
- first_observed_at / last_observed_at use actual meeting timestamps only.
- Invalid transitions are NOT represented as STATE_TRANSITION facts.

Raised exceptions (caught and translated to HTTP errors by the API layer):
  - EntityNotFoundError  → 404  (raised when entity_id is unknown)
"""

import logging
from collections import defaultdict

from app.models.entity import EntityType
from app.models.memory import EntityMemory, EntityMemoryFact, MemoryFactType
from app.models.temporal import TemporalState
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.entity_service import EntityNotFoundError
from app.services.temporal_state_service import TemporalStateService
from app.temporal.state_interpreter import AbstractStateInterpreter
from app.temporal.transition_policy import AbstractTemporalStatePolicy

logger = logging.getLogger(__name__)


class OrganisationalMemoryService:
    """Read-only service that computes the organisational memory for a canonical entity.

    Dependencies are injected via the constructor so this service is fully
    testable without HTTP or real storage.

    This service is deliberately separate from TemporalStateService and
    CorrelationService.  TemporalStateService answers 'How did the state evolve?'
    CorrelationService answers 'What observations exist across meetings?'
    This service answers 'What does the organisation know about this entity?'
    — a higher-level aggregation of both.

    Internal composition
    --------------------
    TemporalStateService is composed internally (not injected) using the same
    pattern that TemporalStateService itself uses to compose CorrelationService.
    This avoids exposing internal service wiring to callers.
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
        # TemporalStateService is composed internally; it itself composes
        # CorrelationService.  We do not inject TemporalStateService directly
        # so that the constructor signature is symmetric with TemporalStateService.
        self._temporal_service = TemporalStateService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=interpreter,
            policy=policy,
        )

    def get_entity_memory(self, entity_id: str) -> EntityMemory:
        """Return the organisational memory record for a canonical entity.

        Aggregates the entity's identity, observation history, temporal lifecycle
        state, and structured memory facts into a single read model.

        Parameters
        ----------
        entity_id:
            ID of the canonical entity to compute memory for.

        Returns
        -------
        EntityMemory
            The entity's complete organisational memory.  facts always contains
            at least CURRENT_STATE.  first_observed_at / last_observed_at are
            None when there are no resolved mentions.

        Raises
        ------
        EntityNotFoundError
            If no canonical entity with *entity_id* exists in the repository.
        """
        # Step 1: Verify entity exists.
        # We do this before calling TemporalStateService to give a clear 404.
        # TemporalStateService also raises EntityNotFoundError, but explicit is
        # better than relying on an internal service's error propagation.
        entity = self._entity_repo.get_by_id(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        logger.info(
            "OrganisationalMemoryService: computing memory for entity %s (%r).",
            entity_id,
            entity.canonical_name,
        )

        # Step 2: Retrieve the entity timeline.
        # TemporalStateService guarantees:
        #   - Only RESOLVED mentions participate.
        #   - Ordered by (meeting_date ASC, meeting_id ASC, mention_id ASC).
        #   - current_state is UNKNOWN when no observations or no state-bearing evidence.
        #   - Each StateObservation carries transition metadata.
        timeline = self._temporal_service.get_entity_timeline(entity_id)
        observations = timeline.timeline

        logger.info(
            "OrganisationalMemoryService: entity %s — %d observation(s), "
            "current_state=%s.",
            entity_id,
            len(observations),
            timeline.current_state.value,
        )

        # Step 3: Derive scalar summary values from the timeline.
        if observations:
            first_observed_at = observations[0].meeting_date
            last_observed_at = observations[-1].meeting_date
        else:
            first_observed_at = None
            last_observed_at = None

        observation_count = len(observations)
        meeting_ids = {obs.meeting_id for obs in observations}
        meeting_count = len(meeting_ids)
        current_state = timeline.current_state

        # Step 4: Build memory facts in deterministic order.
        facts: list[EntityMemoryFact] = []

        # 4a — FIRST_OBSERVED
        if observation_count >= 1:
            first_obs = observations[0]
            facts.append(EntityMemoryFact(
                fact_type=MemoryFactType.FIRST_OBSERVED,
                value=first_obs.meeting_date.isoformat(),
                source_meeting_id=first_obs.meeting_id,
                source_mention_id=first_obs.mention_id,
                observed_at=first_obs.meeting_date,
                detail=first_obs.meeting_title,
            ))

        # 4b — LAST_OBSERVED (only when first != last, i.e. >= 2 observations)
        if observation_count >= 2:
            last_obs = observations[-1]
            facts.append(EntityMemoryFact(
                fact_type=MemoryFactType.LAST_OBSERVED,
                value=last_obs.meeting_date.isoformat(),
                source_meeting_id=last_obs.meeting_id,
                source_mention_id=last_obs.mention_id,
                observed_at=last_obs.meeting_date,
                detail=last_obs.meeting_title,
            ))

        # 4c — CURRENT_STATE (always present, no evidence pointers)
        facts.append(EntityMemoryFact(
            fact_type=MemoryFactType.CURRENT_STATE,
            value=current_state.value,
            source_meeting_id=None,
            source_mention_id=None,
            observed_at=None,
            detail=None,
        ))

        # 4d — STATE_TRANSITION (one per valid transition, chronological)
        # Only valid transitions (transition_occurred=True) are included.
        # Invalid transitions are intentionally excluded — they did not change
        # the entity's state and are already recorded in the /timeline endpoint.
        for obs in observations:
            if obs.transition_occurred:
                facts.append(EntityMemoryFact(
                    fact_type=MemoryFactType.STATE_TRANSITION,
                    value=f"{obs.from_state.value} → {obs.to_state.value}",
                    source_meeting_id=obs.meeting_id,
                    source_mention_id=obs.mention_id,
                    observed_at=obs.meeting_date,
                    detail=obs.meeting_title,
                ))

        # 4e — REPEATED_OBSERVATION (one per meeting with >= 2 observations)
        # Count observations per meeting, then filter and sort.
        meeting_obs_count: dict[str, int] = defaultdict(int)
        # Map meeting_id → (meeting_date, meeting_title) for fact construction.
        meeting_meta: dict[str, tuple] = {}
        for obs in observations:
            meeting_obs_count[obs.meeting_id] += 1
            # Only record the first occurrence of metadata per meeting_id
            # (all observations for the same meeting have identical metadata).
            if obs.meeting_id not in meeting_meta:
                meeting_meta[obs.meeting_id] = (obs.meeting_date, obs.meeting_title)

        # Collect meetings with repeated observations and sort deterministically.
        repeated_meetings = [
            mid for mid, count in meeting_obs_count.items() if count >= 2
        ]
        # Sort by (meeting_date ASC, meeting_id ASC) — matching the codebase convention.
        repeated_meetings.sort(key=lambda mid: (meeting_meta[mid][0], mid))

        for mid in repeated_meetings:
            meeting_date, meeting_title = meeting_meta[mid]
            count = meeting_obs_count[mid]
            facts.append(EntityMemoryFact(
                fact_type=MemoryFactType.REPEATED_OBSERVATION,
                value=str(count),
                source_meeting_id=mid,
                source_mention_id=None,
                observed_at=meeting_date,
                detail=meeting_title,
            ))

        logger.info(
            "OrganisationalMemoryService: entity %s — %d fact(s) constructed.",
            entity_id,
            len(facts),
        )

        # Step 5: Return the EntityMemory read model.
        return EntityMemory(
            entity_id=entity.entity_id,
            canonical_name=entity.canonical_name,
            entity_type=entity.entity_type,
            first_observed_at=first_observed_at,
            last_observed_at=last_observed_at,
            meeting_count=meeting_count,
            observation_count=observation_count,
            current_state=current_state,
            facts=facts,
        )
