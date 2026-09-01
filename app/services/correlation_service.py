"""Correlation service layer.

Orchestrates cross-meeting correlation for canonical entities.

The service:
  - Knows about domain models, repositories, and the correlation read-model.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Is the ONLY place where cross-meeting aggregation is computed.

Cross-Meeting Correlation policy
---------------------------------
1. Fetch the canonical entity by ID.  Raise EntityNotFoundError if absent.
2. Retrieve all mentions associated with that entity_id from the mention
   repository.  (list_by_entity_id returns only mentions whose entity_id
   field is non-None, which is structurally only RESOLVED mentions.)
3. Filter explicitly for resolution_status == RESOLVED as a defense-in-depth
   invariant.  This ensures that even if repository behaviour changes, only
   confirmed resolutions participate in correlation.
4. For each resolved mention, fetch the corresponding Meeting from the
   meeting repository.  If a meeting is missing (data integrity issue),
   log a warning and skip the mention rather than crashing.
5. Construct an EntityObservation for each (mention, meeting) pair.
6. Sort observations chronologically:
     primary   — meeting_date ascending (earliest observations first)
     secondary — meeting_id ascending (deterministic tie-breaker)
     tertiary  — mention_id ascending (handles multiple mentions per meeting)
7. Return EntityCorrelation(entity_id, canonical_name, entity_type, observations).

Invariants
----------
- This service NEVER modifies a mention's resolution_status.
- This service NEVER modifies a mention's entity_id.
- This service NEVER creates or modifies a canonical entity.
- This service NEVER triggers candidate generation.
- This service NEVER triggers candidate scoring.
- This service NEVER triggers the resolution decision engine.
- Correlation is strictly read-only.
- Only RESOLVED mentions (entity_id != None AND status == RESOLVED) participate.
- AMBIGUOUS mentions (entity_id=None) are excluded.
- UNRESOLVED mentions (entity_id=None) are excluded.
- Output ordering is always deterministic given the same repository state.

Raised exceptions (caught and translated to HTTP errors by the API layer):
  - EntityNotFoundError  → 404  (raised when entity_id is unknown)
"""

import logging

from app.models.correlation import EntityCorrelation, EntityObservation
from app.models.entity import ResolutionStatus
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.entity_service import EntityNotFoundError

logger = logging.getLogger(__name__)


class CorrelationService:
    """Read-only service that computes cross-meeting entity correlation.

    Dependencies are injected via the constructor so this service is fully
    testable without HTTP or real storage.

    This service is deliberately separate from EntityService and
    ResolutionService.  EntityService owns entity lifecycle and exact-match
    resolution.  ResolutionService owns resolution decisions.  This service
    owns cross-meeting aggregation — it answers a different question.
    """

    def __init__(
        self,
        entity_repo: AbstractEntityRepository,
        mention_repo: AbstractMentionRepository,
        meeting_repo: AbstractMeetingRepository,
    ) -> None:
        self._entity_repo = entity_repo
        self._mention_repo = mention_repo
        self._meeting_repo = meeting_repo

    def get_entity_correlations(self, entity_id: str) -> EntityCorrelation:
        """Return the cross-meeting correlation history for a canonical entity.

        Retrieves all RESOLVED mentions of the entity, joins each with its
        meeting metadata, and returns them ordered chronologically.

        Parameters
        ----------
        entity_id:
            ID of the canonical entity to correlate across meetings.

        Returns
        -------
        EntityCorrelation
            The entity's cross-meeting history.  observations is empty when
            the entity has no resolved mentions.

        Raises
        ------
        EntityNotFoundError
            If no canonical entity with *entity_id* exists in the repository.
        """
        # Step 1: Fetch and validate the canonical entity.
        entity = self._entity_repo.get_by_id(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        logger.info(
            "CorrelationService: computing correlations for entity %s (%r).",
            entity_id,
            entity.canonical_name,
        )

        # Step 2: Retrieve all mentions associated with this entity.
        # list_by_entity_id returns mentions where entity_id field matches —
        # structurally only RESOLVED mentions have a non-None entity_id.
        mentions = self._mention_repo.list_by_entity_id(entity_id)

        # Step 3: Defense-in-depth filter — only explicitly RESOLVED mentions.
        # This invariant must hold even if repository implementations change.
        resolved_mentions = [
            m for m in mentions
            if m.resolution_status == ResolutionStatus.RESOLVED
        ]

        logger.info(
            "CorrelationService: entity %s — found %d total mentions, "
            "%d are RESOLVED.",
            entity_id,
            len(mentions),
            len(resolved_mentions),
        )

        # Step 4 & 5: Join each resolved mention with its meeting.
        observations: list[EntityObservation] = []
        for mention in resolved_mentions:
            meeting = self._meeting_repo.get_by_id(mention.meeting_id)
            if meeting is None:
                # Data integrity issue: mention references a meeting that no
                # longer exists.  Log and skip — do not crash.
                logger.warning(
                    "CorrelationService: mention %s references meeting %s "
                    "which does not exist in the repository — skipping.",
                    mention.mention_id,
                    mention.meeting_id,
                )
                continue

            observations.append(
                EntityObservation(
                    meeting_id=meeting.meeting_id,
                    meeting_title=meeting.title,
                    meeting_date=meeting.meeting_date,
                    mention_id=mention.mention_id,
                    mention_text=mention.text,
                    source_text=mention.source_text,
                )
            )

        # Step 6: Sort chronologically with deterministic tie-breaking.
        # Primary:   meeting_date ascending  (earliest observations first)
        # Secondary: meeting_id ascending    (stable string sort)
        # Tertiary:  mention_id ascending    (handles same entity in same meeting)
        observations.sort(
            key=lambda obs: (obs.meeting_date, obs.meeting_id, obs.mention_id)
        )

        logger.info(
            "CorrelationService: entity %s — returning %d observation(s).",
            entity_id,
            len(observations),
        )

        # Step 7: Return the correlation result.
        return EntityCorrelation(
            entity_id=entity.entity_id,
            canonical_name=entity.canonical_name,
            entity_type=entity.entity_type,
            observations=observations,
        )
