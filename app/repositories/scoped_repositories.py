"""Tenant-scoped repository views (Stage 24).

Isolation architecture
----------------------
Services (resolution, graphs, intelligence, query, worker pipeline) are
tenant-agnostic: they operate on whatever repositories they are given.
Tenant isolation is enforced HERE, at the data-access boundary, by wrapping
a backing repository in a view pinned to one ``organisation_id``:

- point reads verify the row's ``organisation_id`` and return None on
  mismatch (callers map None to 404 — no cross-tenant existence leak);
- list/enumeration/search methods inject the SQL-level
  ``organisation_id`` predicate (never post-filtered global reads);
- writes stamp the scope organisation and reject objects that already
  carry a *different* organisation (cross-tenant write attempt).

Each view subclasses the corresponding abstract repository so services
accept scoped views transparently.  The unscoped backing repositories keep
their legacy signatures for migration, maintenance, and pre-tenant tests;
production request/worker paths MUST use these views.
"""

from __future__ import annotations

from typing import Callable, Optional

from app.auth.constants import DEFAULT_ORGANISATION_ID
from app.models.background_job import BackgroundJob, BackgroundJobStatus
from app.models.dependency import ExplicitDependency
from app.models.entity import CanonicalEntity, EntityMention, EntityType
from app.models.extraction import ExtractionResult
from app.models.meeting import Meeting
from app.models.relationships import RelationshipType
from app.models.semantic_index import SemanticIndexRecord
from app.repositories.background_job_repository import AbstractBackgroundJobRepository
from app.repositories.dependency_repository import (
    AbstractDependencyRepository,
    filter_current_records,
)
from app.repositories.entity_repository import AbstractEntityRepository
from app.repositories.extraction_repository import AbstractExtractionRepository
from app.repositories.meeting_repository import AbstractMeetingRepository
from app.repositories.mention_repository import (
    AbstractMentionRepository,
    filter_current_mentions,
)
from app.repositories.semantic_index_repository import AbstractSemanticIndexRepository


class TenantScopeMismatchError(ValueError):
    """Raised when a write carries a different organisation than the scope.

    A random ID is never enough to write another tenant's data: the object
    itself must belong to the scope, otherwise this error (mapped to 403)
    is raised before any durable mutation.
    """


def _stamp_org(obj, organisation_id: str):
    """Pin a domain object to the scope organisation.

    Objects built without tenant context carry the bootstrap default and
    are stamped.  Objects already carrying a *different* organisation are
    rejected — this is the cross-tenant write barrier.
    """
    current = getattr(obj, "organisation_id", DEFAULT_ORGANISATION_ID)
    if current != DEFAULT_ORGANISATION_ID and current != organisation_id:
        raise TenantScopeMismatchError(
            f"object '{getattr(obj, 'meeting_id', getattr(obj, 'entity_id', '?'))}' "
            f"belongs to another organisation"
        )
    if current == organisation_id:
        return obj
    return obj.model_copy(update={"organisation_id": organisation_id})


def _in_scope(obj, organisation_id: str) -> bool:
    return getattr(obj, "organisation_id", DEFAULT_ORGANISATION_ID) == organisation_id


class ScopedMeetingRepository(AbstractMeetingRepository):
    def __init__(self, inner: AbstractMeetingRepository, organisation_id: str) -> None:
        self._inner = inner
        self.organisation_id = organisation_id

    def save(self, meeting: Meeting) -> None:
        self._inner.save(_stamp_org(meeting, self.organisation_id))

    def save_and_enqueue(self, meeting: Meeting, job: BackgroundJob) -> None:
        save_and_enqueue = getattr(self._inner, "save_and_enqueue", None)
        if save_and_enqueue is None:
            raise NotImplementedError("backing meeting repository has no save_and_enqueue")
        save_and_enqueue(
            _stamp_org(meeting, self.organisation_id),
            _stamp_org(job, self.organisation_id),
        )

    def get_by_id(self, meeting_id: str) -> Optional[Meeting]:
        meeting = self._inner.get_by_id(meeting_id)
        if meeting is None or not _in_scope(meeting, self.organisation_id):
            return None
        return meeting


class ScopedExtractionRepository(AbstractExtractionRepository):
    def __init__(self, inner: AbstractExtractionRepository, organisation_id: str) -> None:
        self._inner = inner
        self.organisation_id = organisation_id

    def save(self, result: ExtractionResult) -> None:
        self._inner.save(_stamp_org(result, self.organisation_id))

    def get_by_meeting_id(self, meeting_id: str) -> Optional[ExtractionResult]:
        result = self._inner.get_by_meeting_id(meeting_id)
        if result is None or not _in_scope(result, self.organisation_id):
            return None
        return result


class ScopedEntityRepository(AbstractEntityRepository):
    def __init__(self, inner: AbstractEntityRepository, organisation_id: str) -> None:
        self._inner = inner
        self.organisation_id = organisation_id

    def create(self, entity: CanonicalEntity) -> None:
        self._inner.create(_stamp_org(entity, self.organisation_id))

    def get_by_id(self, entity_id: str) -> Optional[CanonicalEntity]:
        entity = self._inner.get_by_id(entity_id)
        if entity is None or not _in_scope(entity, self.organisation_id):
            return None
        return entity

    def find_by_canonical_name(
        self, name: str, entity_type: EntityType, organisation_id: Optional[str] = None
    ) -> Optional[CanonicalEntity]:
        # The scope always wins over a caller-supplied filter: tenant scope
        # is a security property, not a caller preference.
        return self._inner.find_by_canonical_name(name, entity_type, self.organisation_id)

    def list_entities(
        self, entity_type: Optional[EntityType] = None, organisation_id: Optional[str] = None
    ) -> list[CanonicalEntity]:
        return self._inner.list_entities(entity_type, self.organisation_id)

    def add_alias(self, entity_id: str, alias: str) -> Optional[CanonicalEntity]:
        if self.get_by_id(entity_id) is None:
            return None
        updated = self._inner.add_alias(entity_id, alias)
        if updated is None or not _in_scope(updated, self.organisation_id):
            return None
        return updated


class ScopedMentionRepository(AbstractMentionRepository):
    def __init__(self, inner: AbstractMentionRepository, organisation_id: str) -> None:
        self._inner = inner
        self.organisation_id = organisation_id

    def create(self, mention: EntityMention) -> None:
        self._inner.create(_stamp_org(mention, self.organisation_id))

    def get_by_id(self, mention_id: str) -> Optional[EntityMention]:
        mention = self._inner.get_by_id(mention_id)
        if mention is None or not _in_scope(mention, self.organisation_id):
            return None
        return mention

    def list_by_meeting_id(
        self, meeting_id: str, organisation_id: Optional[str] = None
    ) -> list[EntityMention]:
        return self._inner.list_by_meeting_id(meeting_id, self.organisation_id)

    def list_by_entity_id(
        self, entity_id: str, organisation_id: Optional[str] = None
    ) -> list[EntityMention]:
        return self._inner.list_by_entity_id(entity_id, self.organisation_id)

    def list_current_by_meeting_id(
        self,
        meeting_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[EntityMention]:
        return filter_current_mentions(
            self.list_by_meeting_id(meeting_id), current_revision_lookup
        )

    def list_current_by_entity_id(
        self,
        entity_id: str,
        current_revision_lookup: Optional[Callable[[str], Optional[int]]] = None,
    ) -> list[EntityMention]:
        return filter_current_mentions(
            self.list_by_entity_id(entity_id), current_revision_lookup
        )

    def update(self, mention: EntityMention) -> None:
        self._inner.update(_stamp_org(mention, self.organisation_id))


class ScopedDependencyRepository(AbstractDependencyRepository):
    def __init__(self, inner: AbstractDependencyRepository, organisation_id: str) -> None:
        self._inner = inner
        self.organisation_id = organisation_id

    def save(self, dependency: ExplicitDependency) -> None:
        self._inner.save(_stamp_org(dependency, self.organisation_id))

    def get_by_id(self, dependency_id: str) -> Optional[ExplicitDependency]:
        dependency = self._inner.get_by_id(dependency_id)
        if dependency is None or not _in_scope(dependency, self.organisation_id):
            return None
        return dependency

    def list_by_entity_id(
        self, entity_id: str, organisation_id: Optional[str] = None
    ) -> list[ExplicitDependency]:
        return self._inner.list_by_entity_id(entity_id, self.organisation_id)

    def list_by_source_entity_id(
        self, source_entity_id: str, organisation_id: Optional[str] = None
    ) -> list[ExplicitDependency]:
        return self._inner.list_by_source_entity_id(source_entity_id, self.organisation_id)

    def list_by_target_entity_id(
        self, target_entity_id: str, organisation_id: Optional[str] = None
    ) -> list[ExplicitDependency]:
        return self._inner.list_by_target_entity_id(target_entity_id, self.organisation_id)

    def list_by_entity_pair(
        self,
        source_entity_id: str,
        target_entity_id: str,
        relationship_type: Optional[RelationshipType] = None,
        organisation_id: Optional[str] = None,
    ) -> list[ExplicitDependency]:
        return self._inner.list_by_entity_pair(
            source_entity_id, target_entity_id, relationship_type, self.organisation_id
        )

    def list_all(
        self, organisation_id: Optional[str] = None
    ) -> list[ExplicitDependency]:
        return self._inner.list_all(self.organisation_id)

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


class ScopedBackgroundJobRepository(AbstractBackgroundJobRepository):
    """Tenant-scoped job view for API reads.

    The worker itself uses the unscoped repository (it must claim every
    organisation's jobs) and derives tenant scope per job from the durable
    ``job.organisation_id``.  Organisation scope on a job is immutable after
    enqueue, so verify-then-delegate below is race-safe.
    """

    def __init__(self, inner: AbstractBackgroundJobRepository, organisation_id: str) -> None:
        self._inner = inner
        self.organisation_id = organisation_id

    def enqueue(self, job: BackgroundJob) -> BackgroundJob:
        return self._inner.enqueue(_stamp_org(job, self.organisation_id))

    def get(self, job_id: str) -> Optional[BackgroundJob]:
        job = self._inner.get(job_id)
        if job is None or not _in_scope(job, self.organisation_id):
            return None
        return job

    def claim(self, job_id: str, worker_id: str, lease_seconds: int) -> Optional[BackgroundJob]:
        if self.get(job_id) is None:
            return None
        claimed = self._inner.claim(job_id, worker_id, lease_seconds)
        if claimed is None or not _in_scope(claimed, self.organisation_id):
            return None
        return claimed

    def transition(self, job_id: str, status: BackgroundJobStatus, **fields) -> BackgroundJob:
        if self.get(job_id) is None:
            raise TenantScopeMismatchError(f"job '{job_id}' is outside the current organisation")
        return self._inner.transition(job_id, status, **fields)

    def checkpoint(
        self, job_id: str, stage: str, worker_id: Optional[str] = None
    ) -> BackgroundJob:
        if self.get(job_id) is None:
            raise TenantScopeMismatchError(f"job '{job_id}' is outside the current organisation")
        return self._inner.checkpoint(job_id, stage, worker_id=worker_id)

    def recover_stale(self) -> list[BackgroundJob]:
        return [
            job for job in self._inner.recover_stale()
            if _in_scope(job, self.organisation_id)
        ]

    def cancel(self, job_id: str) -> BackgroundJob:
        if self.get(job_id) is None:
            raise TenantScopeMismatchError(f"job '{job_id}' is outside the current organisation")
        return self._inner.cancel(job_id)

    def list(
        self,
        status: Optional[BackgroundJobStatus] = None,
        organisation_id: Optional[str] = None,
    ) -> list[BackgroundJob]:
        return self._inner.list(status, self.organisation_id)


class ScopedSemanticIndexRepository(AbstractSemanticIndexRepository):
    """Tenant-scoped semantic view: every vector operation is org-pinned.

    The durable key is (organisation_id, evidence_id, model, version), so
    identical evidence in two organisations NEVER shares a record, and every
    search filters by tenant at the data-access boundary.
    """

    def __init__(
        self, inner: AbstractSemanticIndexRepository, organisation_id: str
    ) -> None:
        self._inner = inner
        self.organisation_id = organisation_id

    def get_by_composite_key(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
        organisation_id: Optional[str] = None,
    ) -> Optional[SemanticIndexRecord]:
        record = self._inner.get_by_composite_key(
            evidence_id, embedding_model, representation_version, self.organisation_id
        )
        if record is None or not _in_scope(record, self.organisation_id):
            return None
        return record

    def get_by_evidence_id(
        self, evidence_id: str, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
        return self._inner.get_by_evidence_id(evidence_id, self.organisation_id)

    def upsert(self, record: SemanticIndexRecord) -> SemanticIndexRecord:
        return self._inner.upsert(_stamp_org(record, self.organisation_id))

    def delete(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
        organisation_id: Optional[str] = None,
    ) -> bool:
        return self._inner.delete(
            evidence_id, embedding_model, representation_version, self.organisation_id
        )

    def delete_by_evidence_id(
        self, evidence_id: str, organisation_id: Optional[str] = None
    ) -> int:
        return self._inner.delete_by_evidence_id(evidence_id, self.organisation_id)

    def exists(
        self,
        evidence_id: str,
        embedding_model: str,
        representation_version: str,
        organisation_id: Optional[str] = None,
    ) -> bool:
        return self._inner.exists(
            evidence_id, embedding_model, representation_version, self.organisation_id
        )

    def list_by_model(
        self, embedding_model: str, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
        return self._inner.list_by_model(embedding_model, self.organisation_id)

    def list_all(
        self, organisation_id: Optional[str] = None
    ) -> list[SemanticIndexRecord]:
        return self._inner.list_all(self.organisation_id)

    def count(self, organisation_id: Optional[str] = None) -> int:
        return self._inner.count(self.organisation_id)

    def search_similar(
        self,
        query_embedding: list[float],
        embedding_model: str,
        representation_version: str,
        top_k: int,
        min_similarity: float,
        organisation_id: Optional[str] = None,
    ) -> list[tuple[SemanticIndexRecord, float]]:
        return self._inner.search_similar(
            query_embedding,
            embedding_model,
            representation_version,
            top_k,
            min_similarity,
            self.organisation_id,
        )


class TenantRepositories:
    """One pinned view per tenant-owned store, built per request / per job."""

    def __init__(
        self,
        organisation_id: str,
        *,
        meetings: AbstractMeetingRepository,
        extractions: AbstractExtractionRepository,
        entities: AbstractEntityRepository,
        mentions: AbstractMentionRepository,
        dependencies: AbstractDependencyRepository,
        jobs: AbstractBackgroundJobRepository,
        semantic: AbstractSemanticIndexRepository,
    ) -> None:
        self.organisation_id = organisation_id
        self.meetings = ScopedMeetingRepository(meetings, organisation_id)
        self.extractions = ScopedExtractionRepository(extractions, organisation_id)
        self.entities = ScopedEntityRepository(entities, organisation_id)
        self.mentions = ScopedMentionRepository(mentions, organisation_id)
        self.dependencies = ScopedDependencyRepository(dependencies, organisation_id)
        self.jobs = ScopedBackgroundJobRepository(jobs, organisation_id)
        self.semantic = ScopedSemanticIndexRepository(semantic, organisation_id)


    def save_meeting_and_enqueue(self, meeting: Meeting, job: BackgroundJob) -> None:
        """Persist a meeting plus its processing job in one step.

        Uses the atomic save_and_enqueue path when the backing store
        supports it (SQLite), otherwise falls back to scoped save +
        scoped enqueue.  Both objects are tenant-stamped either way.
        """
        try:
            self.meetings.save_and_enqueue(meeting, job)
        except NotImplementedError:
            self.meetings.save(meeting)
            self.jobs.enqueue(job)


def scope_repositories(
    organisation_id: str,
    *,
    meetings: AbstractMeetingRepository,
    extractions: AbstractExtractionRepository,
    entities: AbstractEntityRepository,
    mentions: AbstractMentionRepository,
    dependencies: AbstractDependencyRepository,
    jobs: AbstractBackgroundJobRepository,
    semantic: AbstractSemanticIndexRepository,
) -> TenantRepositories:
    """Build the per-request / per-job tenant view bundle."""
    return TenantRepositories(
        organisation_id,
        meetings=meetings,
        extractions=extractions,
        entities=entities,
        mentions=mentions,
        dependencies=dependencies,
        jobs=jobs,
        semantic=semantic,
    )
