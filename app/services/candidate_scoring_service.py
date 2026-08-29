"""Candidate scoring service layer.

Orchestrates candidate scoring for entity mentions.  The service:
  - Knows about domain models, repositories, the candidate generator,
    and the candidate scorer.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Is the *only* place where candidate scoring is coordinated.

Raised exceptions (caught and translated to HTTP errors by the API layer):
  - MentionNotFoundError → 404

Candidate scoring policy
------------------------
1. Fetch the mention by ID.  Raise MentionNotFoundError if absent.
2. If the mention is already RESOLVED, return (mention, []).
   Reason: the mention has a safe, confirmed identity.  Scoring is
   unnecessary and must not alter resolution state.
3. Fetch all canonical entities of the same entity_type as the mention.
4. Delegate to the configured AbstractCandidateGenerator to obtain the
   candidate shortlist.  (Scoring needs the same set of candidates that
   the generation stage produces.)
5. Fetch the CanonicalEntity objects for each candidate.
6. Delegate to the configured AbstractCandidateScorer.
7. Return (mention, scored_candidates) to the caller.

Invariants
----------
- This service NEVER modifies a mention's resolution_status.
- This service NEVER modifies a mention's entity_id.
- This service NEVER creates or modifies a canonical entity.
- Scoring is strictly read-only with respect to resolution state.
- The actual scoring mathematics live inside the scorer, not here.
"""

import logging

from app.entity_resolution.base import AbstractCandidateGenerator
from app.entity_resolution.scoring_base import AbstractCandidateScorer
from app.models.entity import EntityMention, ScoredEntityCandidate
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.mention_repository import AbstractMentionRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service-layer exception (not HTTP-aware)
# ---------------------------------------------------------------------------

class MentionNotFoundError(Exception):
    """Raised when the requested entity mention does not exist."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class CandidateScoringService:
    """Orchestrates candidate scoring for unresolved entity mentions.

    Dependencies are injected via the constructor so this service is fully
    testable without HTTP or real storage.
    """

    def __init__(
        self,
        mention_repo: AbstractMentionRepository,
        entity_repo: AbstractEntityRepository,
        generator: AbstractCandidateGenerator,
        scorer: AbstractCandidateScorer,
    ) -> None:
        self._mention_repo = mention_repo
        self._entity_repo = entity_repo
        self._generator = generator
        self._scorer = scorer

    def get_scored_candidates(
        self, mention_id: str
    ) -> tuple[EntityMention, list[ScoredEntityCandidate]]:
        """Return the mention and its scored, ranked candidate list.

        Parameters
        ----------
        mention_id:
            ID of the mention to score candidates for.

        Returns
        -------
        (mention, scored_candidates)
            mention           — the EntityMention (unchanged).
            scored_candidates — scored, ordered list of ScoredEntityCandidate
                                objects, possibly empty (when the mention is
                                RESOLVED, has no candidates, or tokens are
                                empty).

        Raises
        ------
        MentionNotFoundError
            If no mention with *mention_id* exists in the repository.
        """
        mention = self._mention_repo.get_by_id(mention_id)
        if mention is None:
            raise MentionNotFoundError(
                f"Mention '{mention_id}' not found."
            )

        # A resolved mention already has a confirmed identity.
        # Scoring is read-only and must not alter resolution state.
        if mention.resolution_status.value == "RESOLVED":
            logger.info(
                "CandidateScoringService: mention %s is already RESOLVED — "
                "returning empty scored candidate list.",
                mention_id,
            )
            return mention, []

        # Fetch only entities of the same type — cross-type scoring is
        # explicitly forbidden.
        entities = self._entity_repo.list_entities(mention.entity_type)

        logger.info(
            "CandidateScoringService: generating candidates for mention %s "
            "(text=%r, type=%s) against %d entities.",
            mention_id,
            mention.text,
            mention.entity_type.value,
            len(entities),
        )

        # Delegate candidate generation to the configured generator.
        candidates = self._generator.generate(mention=mention, entities=entities)

        if not candidates:
            logger.info(
                "CandidateScoringService: no candidates for mention %s — "
                "returning empty scored list.",
                mention_id,
            )
            return mention, []

        # Build the entity list corresponding to the candidate shortlist.
        # We pass the full same-type entity list; the scorer uses an internal
        # map keyed by entity_id.
        logger.info(
            "CandidateScoringService: scoring %d candidate(s) for mention %s.",
            len(candidates),
            mention_id,
        )

        scored = self._scorer.score(
            mention=mention,
            candidates=candidates,
            entities=entities,
        )

        return mention, scored
