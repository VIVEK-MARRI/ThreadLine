"""Thin worker orchestration over the existing ThreadLine domain services.

This module intentionally contains no extraction, resolution, relationship, or
intelligence rules.  It coordinates the existing services after the durable
source record for a meeting has been written.
"""

from datetime import datetime, timezone
from typing import Callable

from app.repositories.extraction_repository import AbstractExtractionRepository
from app.repositories.mention_repository import AbstractMentionRepository
from app.services.dependency_resolution_service import DependencyResolutionService
from app.services.resolution_service import ResolutionService
from app.services.semantic_indexing_service import SemanticIndexingService


class MeetingPipelineOrchestrator:
    """Execute the real processing services used by the durable worker.

    ExtractionResult deliberately has no entity-mention schema.  Therefore
    resolution only acts on mentions already durably recorded for this meeting;
    it never manufactures entity facts from extraction text.

    Every processing execution is tied to an explicit source revision via the
    meeting repository.  Derived and semantic writes are stamped with that
    revision so revision N state can never masquerade as revision N+1.
    """

    def __init__(
        self,
        extraction_repository: AbstractExtractionRepository,
        mention_repository: AbstractMentionRepository,
        resolution_service: ResolutionService,
        dependency_resolution_service: DependencyResolutionService,
        evidence_retrieval_service,
        semantic_indexing_service: SemanticIndexingService,
        derived_services: tuple = (),
        organisation_derived_services: tuple = (),
        observation_service=None,
        ownership_checker: Callable[[], None] | None = None,
        meeting_repository=None,
    ) -> None:
        self._extraction_repository = extraction_repository
        self._mention_repository = mention_repository
        self._resolution_service = resolution_service
        self._dependency_resolution_service = dependency_resolution_service
        self._evidence_retrieval_service = evidence_retrieval_service
        self._semantic_indexing_service = semantic_indexing_service
        self._derived_services = derived_services
        self._organisation_derived_services = organisation_derived_services
        self._observation_service = observation_service
        self._ownership_checker = ownership_checker
        self._meeting_repository = meeting_repository

    def set_ownership_checker(self, checker: Callable[[], None] | None) -> None:
        self._ownership_checker = checker
        # Propagate to every durable writer so a stale worker cannot commit
        # conflicting derived/semantic state through a sub-service.
        for service in (
            self._observation_service,
            self._resolution_service,
            self._dependency_resolution_service,
            self._semantic_indexing_service,
        ):
            setter = getattr(service, "set_ownership_checker", None)
            if callable(setter):
                setter(checker)

    def _assert_owned(self) -> None:
        if self._ownership_checker is not None:
            self._ownership_checker()

    def _current_source_revision(self, meeting_id: str) -> int | None:
        if self._meeting_repository is None:
            return None
        meeting = self._meeting_repository.get_by_id(meeting_id)
        if meeting is None:
            return None
        try:
            return int(getattr(meeting, "source_revision", 1) or 1)
        except (TypeError, ValueError):
            return 1

    def _current_mentions(self, meeting_id: str):
        mentions = self._mention_repository.list_by_meeting_id(meeting_id)
        revision = self._current_source_revision(meeting_id)
        if revision is None:
            return mentions
        return [m for m in mentions if int(getattr(m, "source_revision", 1) or 1) == revision]

    def resolve_meeting(self, meeting_id: str) -> None:
        self._require_extraction(meeting_id)
        self._assert_owned()
        if self._observation_service is not None:
            self._observation_service.observe_meeting(meeting_id)
        for mention in self._current_mentions(meeting_id):
            self._assert_owned()
            self._resolution_service.resolve(mention.mention_id)

    def persist_relationships(self, meeting_id: str) -> None:
        self._require_extraction(meeting_id)
        self._assert_owned()
        # This service accepts only explicit dependency statements and uses
        # deterministic dependency IDs, making worker retries safe.
        # Scope to current-revision resolved mentions so a new source revision
        # never accidentally reuses revision-1 derived data.
        revision = self._current_source_revision(meeting_id)
        if revision is None or self._meeting_repository is None:
            self._dependency_resolution_service.resolve_meeting_dependencies(meeting_id)
            return
        for mention in self._current_mentions(meeting_id):
            if mention.entity_id is None:
                continue
            self._assert_owned()
            self._dependency_resolution_service.resolve_mention_dependencies(mention.mention_id)

    def derive_intelligence(self, meeting_id: str) -> None:
        self._require_extraction(meeting_id)
        self._assert_owned()
        # Existing intelligence is a read-only, deterministic composition over
        # durable source data.  Calling its established service graph keeps the
        # worker on the same semantics as the API without inventing a write model.
        now = datetime.now(timezone.utc)
        entity_ids = sorted({
            mention.entity_id
            for mention in self._current_mentions(meeting_id)
            if mention.entity_id is not None
        })
        for service in self._derived_services:
            for entity_id in entity_ids:
                self._assert_owned()
                service(entity_id, now)
        for service in self._organisation_derived_services:
            self._assert_owned()
            service(now)

    def index_semantic_evidence(self, meeting_id: str) -> None:
        self._require_extraction(meeting_id)
        self._assert_owned()
        source_revision = self._current_source_revision(meeting_id)
        entity_ids = sorted({
            mention.entity_id
            for mention in self._current_mentions(meeting_id)
            if mention.entity_id is not None
        })
        builder = getattr(
            self._evidence_retrieval_service,
            "build_semantic_evidence_for_meeting",
            None,
        )
        if builder is None:
            evidence_items = self._evidence_retrieval_service.build_semantic_corpus(
                datetime.now(timezone.utc)
            )
        else:
            evidence_items = builder(
                meeting_id,
                entity_ids,
                datetime.now(timezone.utc),
            )
        # Index one item at a time so an indexing failure propagates and cannot
        # produce a false checkpoint.
        for item in evidence_items:
            self._assert_owned()
            self._semantic_indexing_service.index_evidence(
                item,
                source_revision=source_revision,
                meeting_id=meeting_id,
            )

    def _require_extraction(self, meeting_id: str) -> None:
        if self._extraction_repository.get_by_meeting_id(meeting_id) is None:
            raise ValueError(
                f"Meeting '{meeting_id}' has no durable extraction result; "
                "later processing cannot run safely."
            )
