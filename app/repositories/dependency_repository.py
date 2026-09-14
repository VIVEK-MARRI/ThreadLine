"""Dependency repository abstraction and in-memory implementation (Stage 15).

Follows the exact same pattern as entity_repository.py, mention_repository.py,
meeting_repository.py, and extraction_repository.py:
  - AbstractDependencyRepository defines the storage contract.
  - InMemoryDependencyRepository provides a dev/test implementation.
  - A persistent backend (PostgreSQL, etc.) can replace the in-memory
    implementation without any changes to the service layer.

ExplicitDependency records are keyed by dependency_id (deterministic hash).
Secondary access patterns (by entity pair, by meeting) use linear scans in
the in-memory implementation; a database backend would use appropriate indices.
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional

from app.models.dependency import ExplicitDependency
from app.models.relationships import RelationshipType


def filter_current_records(
    deps: list[ExplicitDependency],
    current_revision_lookup: Optional[Callable[[str], Optional[int]]],
) -> list[ExplicitDependency]:
    """Keep only dependency records that provably target the current
    authoritative source revision of their owning meeting.

    ``current_revision_lookup`` maps a meeting_id to its current source
    revision (None when unknown).  When the lookup is None no filtering is
    applied (legacy behavior for callers without revision wiring).  A record
    without meeting attribution or a stamped source revision can never prove
    currency and is excluded; a record whose stamped revision differs from
    the authoritative current revision (stale OR future) is excluded so a
    past/future revision never masquerades as current.
    """
    if current_revision_lookup is None:
        return deps
    result: list[ExplicitDependency] = []
    for dep in deps:
        if dep.meeting_id is None or dep.source_revision is None:
            continue
        current = current_revision_lookup(dep.meeting_id)
        if current is None:
            continue
        try:
            if int(dep.source_revision) != int(current):
                continue
        except (TypeError, ValueError):
            continue
        result.append(dep)
    return result


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class AbstractDependencyRepository(ABC):
    """Storage contract for explicit dependency records.

    All methods are intentionally synchronous, matching the repository pattern
    established in the rest of the codebase.
    """

    @abstractmethod
    def save(self, dependency: ExplicitDependency) -> None:
        """Persist an ExplicitDependency record.

        If a record with the same dependency_id already exists it is replaced
        (idempotent upsert semantics — re-processing the same meeting evidence
        produces the same record).
        """
        ...

    @abstractmethod
    def get_by_id(self, dependency_id: str) -> Optional[ExplicitDependency]:
        """Return the dependency record with the given ID, or None."""
        ...

    @abstractmethod
    def list_by_entity_id(
        self, entity_id: str, organisation_id: Optional[str] = None
    ) -> list[ExplicitDependency]:
        """Return all dependency records where entity_id is source OR target.

        When organisation_id is given, only that organisation's records are
        returned.  None preserves the legacy unscoped enumeration.
        """
        ...

    @abstractmethod
    def list_by_source_entity_id(
        self, source_entity_id: str, organisation_id: Optional[str] = None
    ) -> list[ExplicitDependency]:
        """Return all dependency records where the given entity is the source."""
        ...

    @abstractmethod
    def list_by_target_entity_id(
        self, target_entity_id: str, organisation_id: Optional[str] = None
    ) -> list[ExplicitDependency]:
        """Return all dependency records where the given entity is the target."""
        ...

    @abstractmethod
    def list_by_entity_pair(
        self,
        source_entity_id: str,
        target_entity_id: str,
        relationship_type: Optional[RelationshipType] = None,
        organisation_id: Optional[str] = None,
    ) -> list[ExplicitDependency]:
        """Return dependency records between a specific source and target.

        If relationship_type is provided, only records of that type are returned.
        """
        ...

    @abstractmethod
    def list_all(self, organisation_id: Optional[str] = None) -> list[ExplicitDependency]:
        """Return all stored dependency records.

        When organisation_id is given, only that organisation's records are
        returned.  None preserves the legacy unscoped enumeration.
        """
        ...

    @abstractmethod
    def list_current_by_entity_id(
        self,
        entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        """Return current-revision records where entity_id is source OR target.

        See filter_current_records for currency semantics.  Without a lookup
        this behaves like list_by_entity_id.
        """
        ...

    @abstractmethod
    def list_current_by_source_entity_id(
        self,
        source_entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        """Return current-revision records where the given entity is the source.

        See filter_current_records for currency semantics.  Without a lookup
        this behaves like list_by_source_entity_id.
        """
        ...

    @abstractmethod
    def list_current_by_target_entity_id(
        self,
        target_entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        """Return current-revision records where the given entity is the target.

        See filter_current_records for currency semantics.  Without a lookup
        this behaves like list_by_target_entity_id.
        """
        ...

    @abstractmethod
    def list_current_all(
        self,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        """Return all current-revision dependency records.

        See filter_current_records for currency semantics.  Without a lookup
        this behaves like list_all.
        """
        ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

class InMemoryDependencyRepository(AbstractDependencyRepository):
    """Thread-unsafe in-memory store, suitable for development and testing.

    For production use, replace with a persistent backend that implements
    AbstractDependencyRepository.
    """

    def __init__(self) -> None:
        self._store: dict[str, ExplicitDependency] = {}

    def save(self, dependency: ExplicitDependency) -> None:
        """Upsert an ExplicitDependency record keyed by dependency_id."""
        self._store[dependency.dependency_id] = dependency

    def get_by_id(self, dependency_id: str) -> Optional[ExplicitDependency]:
        """Return the record with the given ID, or None."""
        return self._store.get(dependency_id)

    def list_by_entity_id(
        self, entity_id: str, organisation_id: Optional[str] = None
    ) -> list[ExplicitDependency]:
        """Return records where entity_id appears as source or target."""
        rows = [
            d for d in self._store.values()
            if d.source_entity_id == entity_id or d.target_entity_id == entity_id
        ]
        if organisation_id is not None:
            rows = [d for d in rows if d.organisation_id == organisation_id]
        return rows

    def list_by_source_entity_id(
        self, source_entity_id: str, organisation_id: Optional[str] = None
    ) -> list[ExplicitDependency]:
        """Return records where the given entity is the source."""
        rows = [
            d for d in self._store.values()
            if d.source_entity_id == source_entity_id
        ]
        if organisation_id is not None:
            rows = [d for d in rows if d.organisation_id == organisation_id]
        return rows

    def list_by_target_entity_id(
        self, target_entity_id: str, organisation_id: Optional[str] = None
    ) -> list[ExplicitDependency]:
        """Return records where the given entity is the target."""
        rows = [
            d for d in self._store.values()
            if d.target_entity_id == target_entity_id
        ]
        if organisation_id is not None:
            rows = [d for d in rows if d.organisation_id == organisation_id]
        return rows

    def list_by_entity_pair(
        self,
        source_entity_id: str,
        target_entity_id: str,
        relationship_type: Optional[RelationshipType] = None,
        organisation_id: Optional[str] = None,
    ) -> list[ExplicitDependency]:
        """Return records between source and target, optionally filtered by type."""
        results = [
            d for d in self._store.values()
            if d.source_entity_id == source_entity_id
            and d.target_entity_id == target_entity_id
        ]
        if relationship_type is not None:
            results = [d for d in results if d.relationship_type == relationship_type]
        if organisation_id is not None:
            results = [d for d in results if d.organisation_id == organisation_id]
        return results

    def list_all(self, organisation_id: Optional[str] = None) -> list[ExplicitDependency]:
        """Return all stored records."""
        rows = list(self._store.values())
        if organisation_id is not None:
            rows = [d for d in rows if d.organisation_id == organisation_id]
        return rows

    def list_current_by_entity_id(
        self,
        entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        return filter_current_records(
            self.list_by_entity_id(entity_id), current_revision_lookup
        )

    def list_current_by_source_entity_id(
        self,
        source_entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        return filter_current_records(
            self.list_by_source_entity_id(source_entity_id), current_revision_lookup
        )

    def list_current_by_target_entity_id(
        self,
        target_entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        return filter_current_records(
            self.list_by_target_entity_id(target_entity_id), current_revision_lookup
        )

    def list_current_all(
        self,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[ExplicitDependency]:
        return filter_current_records(self.list_all(), current_revision_lookup)
