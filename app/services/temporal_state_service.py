"""Temporal State Engine service layer.

Orchestrates the computation of a temporal lifecycle timeline for a canonical entity.

The service:
  - Knows about domain models, CorrelationService, the state interpreter, and
    the transition policy.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Is the ONLY place where temporal state timelines are computed.

Temporal State Engine pipeline
-------------------------------
1. Fetch the canonical entity by ID.  Raise EntityNotFoundError if absent.
2. Compute cross-meeting correlations via CorrelationService.  The result
   already contains chronologically ordered, RESOLVED-only observations.
3. For each observation (in chronological order):
   a. Interpret the state from the observation's source_text using the
      configured AbstractStateInterpreter.
   b. Apply the transition policy (AbstractTemporalStatePolicy) to determine
      whether the interpreted state represents a valid, new transition.
   c. Record a StateObservation capturing the interpretation and transition metadata.
4. Compute summary statistics (current_state, observation_count, transition_count).
5. Return EntityTimeline.

Invariants
----------
- This service NEVER modifies a mention's resolution_status.
- This service NEVER modifies a mention's entity_id.
- This service NEVER creates or modifies a canonical entity.
- This service NEVER triggers candidate generation.
- This service NEVER triggers candidate scoring.
- This service NEVER triggers the resolution decision engine.
- This service NEVER triggers re-extraction.
- Temporal state computation is strictly read-only.
- Only RESOLVED mentions (via CorrelationService) participate.
- The timeline is always deterministically ordered by
    (meeting_date ASC, meeting_id ASC, mention_id ASC).
- The same repository state always produces exactly the same timeline.

Raised exceptions (caught and translated to HTTP errors by the API layer):
  - EntityNotFoundError  → 404  (raised when entity_id is unknown)
"""

import logging

from app.models.temporal import EntityTimeline, StateObservation, TemporalState
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.entity_service import EntityNotFoundError
from app.services.correlation_service import CorrelationService
from app.temporal.state_interpreter import AbstractStateInterpreter
from app.temporal.transition_policy import AbstractTemporalStatePolicy

logger = logging.getLogger(__name__)


class TemporalStateService:
    """Read-only service that computes the temporal lifecycle timeline for a canonical entity.

    Dependencies are injected via the constructor so this service is fully
    testable without HTTP or real storage.

    This service is deliberately separate from CorrelationService.
    CorrelationService owns cross-meeting aggregation — it answers 'What
    observations exist?'  TemporalStateService owns state interpretation
    and transition tracking — it answers 'How did the state evolve?'
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
        self._mention_repo = mention_repo
        self._meeting_repo = meeting_repo
        self._interpreter = interpreter
        self._policy = policy
        # CorrelationService is composed internally; it is not injected
        # because its dependencies are already available here.
        self._correlation_service = CorrelationService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
        )

    def get_entity_timeline(self, entity_id: str) -> EntityTimeline:
        """Return the temporal lifecycle timeline for a canonical entity.

        Fetches the cross-meeting correlation, interprets the state of each
        observation, applies the transition policy, and returns the complete
        timeline.

        Parameters
        ----------
        entity_id:
            ID of the canonical entity to compute the timeline for.

        Returns
        -------
        EntityTimeline
            The entity's complete temporal lifecycle history.  timeline is
            empty when the entity has no resolved mentions.  current_state
            is UNKNOWN when no observations or no state-bearing evidence
            was found.

        Raises
        ------
        EntityNotFoundError
            If no canonical entity with *entity_id* exists in the repository.
        """
        # Step 1: Fetch the entity and fail fast if unknown.
        # We do this before calling CorrelationService to give a clear 404
        # rather than an internal error.  CorrelationService would also raise
        # EntityNotFoundError, but we raise it explicitly here for clarity.
        entity = self._entity_repo.get_by_id(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        logger.info(
            "TemporalStateService: computing timeline for entity %s (%r).",
            entity_id,
            entity.canonical_name,
        )

        # Step 2: Get all correlated (RESOLVED, chronologically ordered) observations.
        # CorrelationService handles the entity lookup, mention filtering,
        # meeting join, and ordering.  We rely on its invariants:
        #   - Only RESOLVED mentions participate.
        #   - Ordered by (meeting_date ASC, meeting_id ASC, mention_id ASC).
        correlation = self._correlation_service.get_entity_correlations(entity_id)
        observations = correlation.observations

        logger.info(
            "TemporalStateService: entity %s — %d correlated observation(s).",
            entity_id,
            len(observations),
        )

        # Step 3: Replay observations through the interpreter and policy.
        current_state: TemporalState = TemporalState.UNKNOWN
        timeline: list[StateObservation] = []

        for index, obs in enumerate(observations):
            # Step 3a: Interpret the state from the evidence text.
            interpreted_state = self._interpreter.interpret(obs.source_text)

            # Step 3b: Apply the transition policy.
            from_state = current_state
            result = self._policy.apply(
                current_state=current_state,
                new_state=interpreted_state,
            )
            # Update the running state machine.
            current_state = result.current_state

            # Step 3c: Record the StateObservation.
            state_obs = StateObservation(
                observation_index=index,
                meeting_id=obs.meeting_id,
                meeting_title=obs.meeting_title,
                meeting_date=obs.meeting_date,
                mention_id=obs.mention_id,
                evidence_text=obs.source_text,
                interpreted_state=interpreted_state,
                transition_occurred=result.transition_occurred,
                from_state=from_state,
                to_state=result.current_state,
                is_valid_transition=result.is_valid,
                transition_skipped_reason=result.reason,
            )
            timeline.append(state_obs)

        # Step 4: Compute summary statistics.
        transition_count = sum(1 for s in timeline if s.transition_occurred)

        logger.info(
            "TemporalStateService: entity %s — current_state=%s, "
            "%d observation(s), %d transition(s).",
            entity_id,
            current_state.value,
            len(timeline),
            transition_count,
        )

        # Step 5: Return the complete EntityTimeline.
        return EntityTimeline(
            entity_id=entity.entity_id,
            canonical_name=entity.canonical_name,
            entity_type=entity.entity_type,
            current_state=current_state,
            observation_count=len(timeline),
            transition_count=transition_count,
            timeline=timeline,
        )
