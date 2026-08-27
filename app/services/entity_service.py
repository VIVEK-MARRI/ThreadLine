"""Entity service layer.

Orchestrates all business logic for canonical entity management and entity
mention registration.  The service:
  - Knows about domain models, repositories, and normalisation rules.
  - Has no awareness of HTTP, Pydantic API schemas, or storage mechanics.
  - Is the *only* place where resolution decisions are made.

Raised exceptions (caught and translated to HTTP errors by the API layer):
  - EntityNotFoundError → 404

Resolution policy
-----------------
Today's resolution is intentionally conservative — exact match only.

A mention is resolved to a canonical entity if and only if its normalised
text matches the entity's normalised canonical_name or one of its aliases,
within the same entity_type.

If no exact match is found, the mention is stored as UNRESOLVED.
The system does NOT automatically create a new entity for an unresolved
mention.  Automatic entity creation only happens via an explicit API call.

This policy prevents incorrect entity merges, which are harder to undo
than simply leaving a mention unresolved.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.models.entity import (
    CanonicalEntity,
    EntityMention,
    EntityType,
    ResolutionStatus,
)
from app.repositories.entity_repository import AbstractEntityRepository, _normalize
from app.repositories.mention_repository import AbstractMentionRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service-layer exception (not HTTP-aware)
# ---------------------------------------------------------------------------

class EntityNotFoundError(Exception):
    """Raised when the requested canonical entity does not exist."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class EntityService:
    """Encapsulates all entity registry and mention registration operations."""

    def __init__(
        self,
        entity_repo: AbstractEntityRepository,
        mention_repo: AbstractMentionRepository,
    ) -> None:
        self._entity_repo = entity_repo
        self._mention_repo = mention_repo

    # ------------------------------------------------------------------
    # Canonical entity operations
    # ------------------------------------------------------------------

    def create_entity(
        self,
        entity_type: EntityType,
        canonical_name: str,
    ) -> tuple[CanonicalEntity, bool]:
        """Create a new canonical entity, or return an existing one.

        Normalises canonical_name before checking for duplicates.  If an
        entity of the same type with the same normalised canonical_name
        (or matching alias) already exists, that entity is returned and
        ``created`` is False.

        Parameters
        ----------
        entity_type:
            The category of this entity.
        canonical_name:
            The preferred name for this entity.  Will be stripped and
            whitespace-collapsed before storage and comparison.

        Returns
        -------
        (entity, created)
            entity  — the canonical entity (new or existing).
            created — True if a new entity was created, False if an
                      existing one was returned.
        """
        normalised_name = _normalize(canonical_name)

        existing = self._entity_repo.find_by_canonical_name(
            normalised_name, entity_type
        )
        if existing is not None:
            logger.info(
                "EntityService: returning existing entity %s for name=%r type=%s",
                existing.entity_id,
                canonical_name,
                entity_type.value,
            )
            return existing, False

        entity = CanonicalEntity(
            entity_id=str(uuid.uuid4()),
            entity_type=entity_type,
            canonical_name=normalised_name,
            aliases=[],
            created_at=datetime.now(tz=timezone.utc),
        )
        self._entity_repo.create(entity)
        logger.info(
            "EntityService: created entity %s name=%r type=%s",
            entity.entity_id,
            normalised_name,
            entity_type.value,
        )
        return entity, True

    def get_entity(self, entity_id: str) -> CanonicalEntity:
        """Return a canonical entity by ID.

        Raises
        ------
        EntityNotFoundError
            If no entity with the given ID exists.
        """
        entity = self._entity_repo.get_by_id(entity_id)
        if entity is None:
            raise EntityNotFoundError(
                f"Entity '{entity_id}' not found."
            )
        return entity

    def list_entities(
        self, entity_type: Optional[EntityType] = None
    ) -> list[CanonicalEntity]:
        """Return all entities, optionally filtered by entity_type."""
        return self._entity_repo.list_entities(entity_type)

    # ------------------------------------------------------------------
    # Mention registration
    # ------------------------------------------------------------------

    def register_mention(
        self,
        entity_type: EntityType,
        text: str,
        meeting_id: str,
        source_text: str,
    ) -> EntityMention:
        """Register an observed mention and attempt exact-match resolution.

        Resolution rules
        ----------------
        1. Normalise the mention text.
        2. Search the entity registry for an exact match against
           canonical_name or any alias of an entity of the same type.
        3. If a match is found  → RESOLVED, entity_id is set.
        4. If no match is found → UNRESOLVED, entity_id remains None.
           A new canonical entity is NOT automatically created.

        This conservative approach ensures incorrect merges never happen
        silently.  A human or future pipeline stage can promote an
        unresolved mention to a resolved one later.

        Parameters
        ----------
        entity_type:
            The category this mention is believed to refer to.
        text:
            The surface form as it appeared in the transcript.
        meeting_id:
            ID of the source meeting.
        source_text:
            The surrounding transcript excerpt (evidence).

        Returns
        -------
        EntityMention
            The stored mention, including resolution_status and entity_id.
        """
        normalised_text = _normalize(text)

        matched_entity = self._entity_repo.find_by_canonical_name(
            normalised_text, entity_type
        )

        if matched_entity is not None:
            entity_id: Optional[str] = matched_entity.entity_id
            status = ResolutionStatus.RESOLVED
            logger.info(
                "EntityService: mention %r resolved to entity %s",
                text,
                matched_entity.entity_id,
            )
        else:
            entity_id = None
            status = ResolutionStatus.UNRESOLVED
            logger.info(
                "EntityService: mention %r is unresolved (no exact match for type=%s)",
                text,
                entity_type.value,
            )

        mention = EntityMention(
            mention_id=str(uuid.uuid4()),
            entity_type=entity_type,
            text=text,
            meeting_id=meeting_id,
            source_text=source_text,
            entity_id=entity_id,
            resolution_status=status,
            created_at=datetime.now(tz=timezone.utc),
        )
        self._mention_repo.create(mention)
        return mention
