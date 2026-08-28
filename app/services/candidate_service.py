"""Candidate service layer.

Orchestrates candidate generation for entity mentions.  The service:
  - Knows about domain models, repositories, and the candidate generator.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Is the *only* place where candidate generation is coordinated.

Raised exceptions (caught and translated to HTTP errors by the API layer):
  - MentionNotFoundError → 404

Candidate generation policy
----------------------------
1. Fetch the mention by ID.  Raise MentionNotFoundError if absent.
2. If the mention is already RESOLVED, return an empty candidate list.
   Reason: the mention has a safe, confirmed identity.  Candidate generation
   is unnecessary and would not change the resolution state.
3. Fetch all canonical entities of the same entity_type as the mention.
4. Delegate to the configured AbstractCandidateGenerator.
5. Return the (mention, candidates) tuple to the caller.

Invariants
----------
- This service NEVER modifies a mention's resolution_status.
- This service NEVER modifies a mention's entity_id.
- This service NEVER creates or modifies a canonical entity.
- Candidate generation is strictly read-only with respect to resolution state.
"""

import logging

from app.entity_resolution.base import AbstractCandidateGenerator
from app.models.entity import EntityCandidate, EntityMention
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

class CandidateService:
    """Orchestrates candidate generation for unresolved entity mentions.

    Dependencies are injected via the constructor so this service is fully
    testable without HTTP or real storage.
    """

    def __init__(
        self,
        mention_repo: AbstractMentionRepository,
        entity_repo: AbstractEntityRepository,
        generator: AbstractCandidateGenerator,
    ) -> None:
        self._mention_repo = mention_repo
        self._entity_repo = entity_repo
        self._generator = generator

    def get_candidates(
        self, mention_id: str
    ) -> tuple[EntityMention, list[EntityCandidate]]:
        """Return the mention and its ordered candidate list.

        Parameters
        ----------
        mention_id:
            ID of the mention to generate candidates for.

        Returns
        -------
        (mention, candidates)
            mention    — the EntityMention (unchanged).
            candidates — ordered list of EntityCandidate objects, possibly
                         empty (when mention is RESOLVED or no entity has
                         meaningful lexical overlap).

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
        # Candidate generation is read-only and must not alter resolution state,
        # so we return an empty list without calling the generator.
        if mention.resolution_status.value == "RESOLVED":
            logger.info(
                "CandidateService: mention %s is already RESOLVED — "
                "returning empty candidate list.",
                mention_id,
            )
            return mention, []

        # Fetch only entities of the same type — cross-type comparison is
        # explicitly forbidden by the candidate generation invariants.
        entities = self._entity_repo.list_entities(mention.entity_type)

        logger.info(
            "CandidateService: generating candidates for mention %s "
            "(text=%r, type=%s) against %d entities.",
            mention_id,
            mention.text,
            mention.entity_type.value,
            len(entities),
        )

        candidates = self._generator.generate(mention=mention, entities=entities)
        return mention, candidates
