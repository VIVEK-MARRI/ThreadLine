"""Entity mention repository abstraction and in-memory implementation.

Follows the exact same pattern as entity_repository.py, meeting_repository.py,
and extraction_repository.py:
  - AbstractMentionRepository defines the storage contract.
  - InMemoryMentionRepository provides a dev/test implementation.
  - A persistent backend (PostgreSQL, etc.) can replace the in-memory
    implementation without any changes to the service layer.

Mentions are keyed by mention_id.  Secondary access patterns (by meeting or
by entity) are supported via linear scans in the in-memory implementation;
a database backend would use appropriate indices.
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.models.entity import EntityMention


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class AbstractMentionRepository(ABC):
    """Storage contract for entity mentions.

    All methods are intentionally synchronous, matching the repository pattern
    established in the rest of the codebase.
    """

    @abstractmethod
    def create(self, mention: EntityMention) -> None:
        """Persist a new entity mention."""
        ...

    @abstractmethod
    def get_by_id(self, mention_id: str) -> Optional[EntityMention]:
        """Return the mention with the given ID, or None."""
        ...

    @abstractmethod
    def list_by_meeting_id(self, meeting_id: str) -> list[EntityMention]:
        """Return all mentions observed in a specific meeting."""
        ...

    @abstractmethod
    def list_by_entity_id(self, entity_id: str) -> list[EntityMention]:
        """Return all mentions that resolved to a specific canonical entity."""
        ...

    @abstractmethod
    def update(self, mention: EntityMention) -> None:
        """Persist an updated mention, replacing the existing record.

        The mention_id must already exist in the repository.  If it does not,
        implementations should raise a KeyError or silently no-op — callers
        must ensure the mention exists before calling update.

        This method is intentionally minimal: it replaces the entire record
        rather than supporting partial field updates.  This keeps the
        repository contract simple and avoids partial-update ambiguity.
        """


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

class InMemoryMentionRepository(AbstractMentionRepository):
    """Thread-unsafe in-memory store, suitable for development and testing.

    For production use, replace with a persistent backend that implements
    AbstractMentionRepository.
    """

    def __init__(self) -> None:
        self._store: dict[str, EntityMention] = {}

    def create(self, mention: EntityMention) -> None:
        """Store a new mention keyed by mention_id."""
        self._store[mention.mention_id] = mention

    def get_by_id(self, mention_id: str) -> Optional[EntityMention]:
        """Return the mention with the given ID, or None."""
        return self._store.get(mention_id)

    def list_by_meeting_id(self, meeting_id: str) -> list[EntityMention]:
        """Return all mentions from a specific meeting (linear scan)."""
        return [m for m in self._store.values() if m.meeting_id == meeting_id]

    def list_by_entity_id(self, entity_id: str) -> list[EntityMention]:
        """Return all resolved mentions for a specific entity (linear scan)."""
        return [m for m in self._store.values() if m.entity_id == entity_id]

    def update(self, mention: EntityMention) -> None:
        """Replace the stored mention record with the updated version.

        Silently no-ops if the mention_id is not present (mirrors the
        permissive in-memory pattern used elsewhere in the codebase).
        """
        self._store[mention.mention_id] = mention
