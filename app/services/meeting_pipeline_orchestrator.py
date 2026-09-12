"""Thin worker orchestration over the existing ThreadLine domain services.

This module intentionally contains no extraction, resolution, relationship, or
intelligence rules.  It coordinates the existing services after the durable
source record for a meeting has been written.
"""

from datetime import datetime, timezone

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
    ) -> None:
        self._extraction_repository = extraction_repository
        self._mention_repository = mention_repository
        self._resolution_service = resolution_service
        self._dependency_resolution_service = dependency_resolution_service
        self._evidence_retrieval_service = evidence_retrieval_service
        self._semantic_indexing_service = semantic_indexing_service
        self._derived_services = derived_services

    def resolve_meeting(self, meeting_id: str) -> None:
        self._require_extraction(meeting_id)
        for mention in self._mention_repository.list_by_meeting_id(meeting_id):
            self._resolution_service.resolve(mention.mention_id)

    def persist_relationships(self, meeting_id: str) -> None:
        self._require_extraction(meeting_id)
        # This service accepts only explicit dependency statements and uses
        # deterministic dependency IDs, making worker retries safe.
        self._dependency_resolution_service.resolve_meeting_dependencies(meeting_id)

    def derive_intelligence(self, meeting_id: str) -> None:
        self._require_extraction(meeting_id)
        # Existing intelligence is a read-only, deterministic composition over
        # durable source data.  Calling its established service graph keeps the
        # worker on the same semantics as the API without inventing a write model.
        now = datetime.now(timezone.utc)
        entity_ids = sorted({
            mention.entity_id
            for mention in self._mention_repository.list_by_meeting_id(meeting_id)
            if mention.entity_id is not None
        })
        for service in self._derived_services:
            for entity_id in entity_ids:
                service(entity_id, now)

    def index_semantic_evidence(self, meeting_id: str) -> None:
        self._require_extraction(meeting_id)
        # Evidence construction is authoritative; the semantic repository is
        # derived data.  Index one item at a time so an indexing failure is not
        # swallowed by the batch helper and cannot produce a false checkpoint.
        evidence_items = self._evidence_retrieval_service.build_semantic_corpus(
            datetime.now(timezone.utc)
        )
        for item in evidence_items:
            self._semantic_indexing_service.index_evidence(item)

    def _require_extraction(self, meeting_id: str) -> None:
        if self._extraction_repository.get_by_meeting_id(meeting_id) is None:
            raise ValueError(
                f"Meeting '{meeting_id}' has no durable extraction result; "
                "later processing cannot run safely."
            )
