"""Resolution Decision service layer.

Orchestrates the Resolution Decision stage of the entity-resolution pipeline.
The service:
  - Knows about domain models, repositories, the scoring service, and the
    resolution policy.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Is the ONLY place where the resolution decision mutation is applied.

Resolution Decision policy
--------------------------
1. Fetch the mention by ID.  Raise MentionNotFoundError if absent.
2. If the mention is already RESOLVED:
   - Do NOT downgrade it (state invariants 1 & 2).
   - Return a ResolutionDecision with outcome=RESOLVED reflecting current state.
3. Generate and score candidates via CandidateScoringService.
4. Apply the configured AbstractResolutionPolicy.
5. If decision is RESOLVED:
   - Verify the selected entity_id is in the candidate list (invariant 7).
   - Update the mention: set entity_id and resolution_status = RESOLVED.
   - Persist the updated mention.
6. If decision is AMBIGUOUS:
   - Do NOT assign entity_id (invariant 4).
   - Update the mention: resolution_status = AMBIGUOUS, entity_id = None.
   - Persist the updated mention.
7. If decision is UNRESOLVED:
   - Do NOT assign entity_id (invariant 5).
   - Leave the mention as-is (no mutation needed).
8. Return the explainable ResolutionDecision.

Invariants enforced
-------------------
INVARIANT 1: RESOLVED must never become UNRESOLVED.
INVARIANT 2: RESOLVED must never become AMBIGUOUS.
INVARIANT 3: UNRESOLVED may become RESOLVED only via an explicit decision.
INVARIANT 4: AMBIGUOUS mentions have entity_id = None.
INVARIANT 5: UNRESOLVED mentions have entity_id = None.
INVARIANT 6: Resolution never creates a new canonical entity.
INVARIANT 7: Resolution only selects an entity from the scored candidate list.
INVARIANT 8: The decision is deterministic.
"""

import logging

from app.entity_resolution.resolution_policy import AbstractResolutionPolicy
from app.models.entity import ResolutionDecision, ResolutionOutcome, ResolutionStatus
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.candidate_scoring_service import CandidateScoringService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service-layer exception (not HTTP-aware)
# ---------------------------------------------------------------------------

class MentionNotFoundError(Exception):
    """Raised when the requested entity mention does not exist."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ResolutionService:
    """Orchestrates the Resolution Decision stage for entity mentions.

    Dependencies are injected via the constructor so this service is fully
    testable without HTTP or real storage.

    This service is deliberately separate from EntityService and
    CandidateScoringService.  Each stage of the pipeline has a single,
    focused service — they are not merged into an omnibus class.
    """

    def __init__(
        self,
        mention_repo: AbstractMentionRepository,
        entity_repo: AbstractEntityRepository,
        scoring_service: CandidateScoringService,
        policy: AbstractResolutionPolicy,
    ) -> None:
        self._mention_repo = mention_repo
        self._entity_repo = entity_repo
        self._scoring_service = scoring_service
        self._policy = policy

    def resolve(self, mention_id: str) -> ResolutionDecision:
        """Apply the resolution policy to a mention and return the decision.

        This is the only method that may mutate a mention's resolution_status
        or entity_id as a result of the decision engine.  Candidate generation
        and scoring remain read-only.

        Parameters
        ----------
        mention_id:
            ID of the mention to resolve.

        Returns
        -------
        ResolutionDecision
            An explainable decision with outcome, selected_entity_id, scores,
            margin, and human-readable reason.

        Raises
        ------
        MentionNotFoundError
            If no mention with *mention_id* exists in the repository.
        """
        # Step 1 — Fetch the mention.
        mention = self._mention_repo.get_by_id(mention_id)
        if mention is None:
            raise MentionNotFoundError(f"Mention '{mention_id}' not found.")

        # Step 2 — Guard: already-RESOLVED mentions must not be downgraded.
        if mention.resolution_status == ResolutionStatus.RESOLVED:
            logger.info(
                "ResolutionService: mention %s is already RESOLVED "
                "(entity_id=%s) — returning existing state without modification.",
                mention_id,
                mention.entity_id,
            )
            return ResolutionDecision(
                mention_id=mention_id,
                outcome=ResolutionOutcome.RESOLVED,
                selected_entity_id=mention.entity_id,
                top_score=None,
                second_score=None,
                score_margin=None,
                reason=(
                    "Mention is already RESOLVED.  "
                    "The resolution engine does not downgrade confirmed resolutions."
                ),
            )

        # Step 3 — Score candidates (CandidateScoringService is read-only).
        _, scored_candidates = self._scoring_service.get_scored_candidates(mention_id)

        # Step 4 — Apply the resolution policy.
        decision = self._policy.decide(
            mention_id=mention_id,
            scored_candidates=scored_candidates,
        )

        logger.info(
            "ResolutionService: mention %s → outcome=%s selected_entity_id=%s.",
            mention_id,
            decision.outcome.value,
            decision.selected_entity_id,
        )

        # Step 5 — Mutate and persist based on the decision.
        if decision.outcome == ResolutionOutcome.RESOLVED:
            # Invariant 7: the selected entity must be in the candidate list.
            candidate_ids = {sc.entity_id for sc in scored_candidates}
            assert decision.selected_entity_id in candidate_ids, (
                f"Resolution invariant violated: selected entity "
                f"'{decision.selected_entity_id}' is not in the scored candidate list "
                f"{candidate_ids} for mention '{mention_id}'."
            )

            updated_mention = mention.model_copy(
                update={
                    "entity_id": decision.selected_entity_id,
                    "resolution_status": ResolutionStatus.RESOLVED,
                }
            )
            self._mention_repo.update(updated_mention)
            logger.info(
                "ResolutionService: mention %s resolved to entity %s.",
                mention_id,
                decision.selected_entity_id,
            )

        elif decision.outcome == ResolutionOutcome.AMBIGUOUS:
            # Invariant 4: AMBIGUOUS mentions must have entity_id = None.
            updated_mention = mention.model_copy(
                update={
                    "entity_id": None,
                    "resolution_status": ResolutionStatus.AMBIGUOUS,
                }
            )
            self._mention_repo.update(updated_mention)
            logger.info(
                "ResolutionService: mention %s marked AMBIGUOUS.",
                mention_id,
            )

        else:
            # UNRESOLVED — no mutation; mention stays as-is.
            logger.info(
                "ResolutionService: mention %s remains UNRESOLVED.",
                mention_id,
            )

        return decision
