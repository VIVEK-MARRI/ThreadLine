"""Canonical entity repository abstraction and in-memory implementation.

Follows the exact same pattern as meeting_repository.py and
extraction_repository.py:
  - AbstractEntityRepository defines the storage contract.
  - InMemoryEntityRepository provides a dev/test implementation.
  - When we move to a persistent backend, we implement the same abstract
    interface without touching the service layer.

The find_by_canonical_name method performs case-insensitive, whitespace-
normalised lookup across both canonical_name and aliases, using the same
_normalize() logic that the service uses.  This ensures the service and
repository always agree on what counts as an exact match.
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.models.entity import CanonicalEntity, EntityType


# ---------------------------------------------------------------------------
# Shared normalisation helper
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Case-fold and collapse whitespace for exact-match comparison.

    This is intentionally conservative:
      - Strip leading/trailing whitespace
      - Collapse repeated internal spaces
      - Lowercase

    It does NOT perform fuzzy, phonetic, or semantic normalisation.
    """
    return " ".join(text.strip().lower().split())


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class AbstractEntityRepository(ABC):
    """Storage contract for canonical entities.

    All methods are intentionally synchronous for now.  When we introduce
    async persistence (e.g. asyncpg), this interface will be updated once
    and all callers will follow.
    """

    @abstractmethod
    def create(self, entity: CanonicalEntity) -> None:
        """Persist a new canonical entity."""
        ...

    @abstractmethod
    def get_by_id(self, entity_id: str) -> Optional[CanonicalEntity]:
        """Return the entity with the given ID, or None."""
        ...

    @abstractmethod
    def find_by_canonical_name(
        self, name: str, entity_type: EntityType
    ) -> Optional[CanonicalEntity]:
        """Return an entity whose canonical_name or any alias matches *name*
        (after normalisation) within the given entity_type, or None.

        Only entities of the specified entity_type are searched.  A mention of
        "Rahul" for entity_type=PERSON must not resolve to an ISSUE named "Rahul".
        """
        ...

    @abstractmethod
    def list_entities(
        self, entity_type: Optional[EntityType] = None
    ) -> list[CanonicalEntity]:
        """Return all entities, optionally filtered by entity_type."""
        ...

    @abstractmethod
    def add_alias(self, entity_id: str, alias: str) -> Optional[CanonicalEntity]:
        """Add *alias* to the entity's alias list.

        Returns the updated entity, or None if entity_id does not exist.
        Adding an alias does NOT merge other entities.
        """
        ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

class InMemoryEntityRepository(AbstractEntityRepository):
    """Thread-unsafe in-memory store, suitable for development and testing.

    For production use, replace with a persistent backend that implements
    AbstractEntityRepository.
    """

    def __init__(self) -> None:
        self._store: dict[str, CanonicalEntity] = {}

    def create(self, entity: CanonicalEntity) -> None:
        """Store a new canonical entity keyed by entity_id."""
        self._store[entity.entity_id] = entity

    def get_by_id(self, entity_id: str) -> Optional[CanonicalEntity]:
        """Return the entity with the given ID, or None."""
        return self._store.get(entity_id)

    def find_by_canonical_name(
        self, name: str, entity_type: EntityType
    ) -> Optional[CanonicalEntity]:
        """Search canonical_name and aliases (case-insensitive, normalised).

        Returns the first matching entity of the given type, or None.
        The search is O(n) over all stored entities — acceptable for the
        in-memory implementation.  A database backend would use an index.
        """
        target = _normalize(name)
        for entity in self._store.values():
            if entity.entity_type != entity_type:
                continue
            if _normalize(entity.canonical_name) == target:
                return entity
            for alias in entity.aliases:
                if _normalize(alias) == target:
                    return entity
        return None

    def list_entities(
        self, entity_type: Optional[EntityType] = None
    ) -> list[CanonicalEntity]:
        """Return all entities, optionally filtered by entity_type."""
        entities = list(self._store.values())
        if entity_type is not None:
            entities = [e for e in entities if e.entity_type == entity_type]
        return entities

    def add_alias(self, entity_id: str, alias: str) -> Optional[CanonicalEntity]:
        """Append *alias* to the entity's alias list and return the updated entity.

        Returns None if entity_id does not exist.
        """
        entity = self._store.get(entity_id)
        if entity is None:
            return None
        # Pydantic v2 models are not mutable by default; rebuild with updated aliases.
        updated = entity.model_copy(update={"aliases": [*entity.aliases, alias]})
        self._store[entity_id] = updated
        return updated
