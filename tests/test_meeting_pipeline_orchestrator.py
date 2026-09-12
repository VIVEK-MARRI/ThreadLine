"""Real-service worker orchestration coverage for Stage 23.2."""

from datetime import datetime, timezone

import pytest

from app.entity_resolution.lexical_candidate_generator import LexicalCandidateGenerator
from app.entity_resolution.lexical_candidate_scorer import LexicalCandidateScorer
from app.entity_resolution.resolution_policy import ThresholdResolutionPolicy
from app.extraction.fake_provider import FakeExtractionProvider
from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.models.entity import EntityType
from app.models.extraction import Evidence, ExtractionResult, Issue
from app.models.natural_language import EvidenceItem, EvidenceType
from app.models.meeting import Meeting
from app.providers.fake_embedding_provider import FakeEmbeddingProvider
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.background_job_repository import InMemoryBackgroundJobRepository
from app.repositories.dependency_repository import InMemoryDependencyRepository
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.extraction_repository import InMemoryExtractionRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.repositories.semantic_index_repository import InMemorySemanticIndexRepository
from app.repositories.semantic_index_repository import JsonFileSemanticIndexRepository
from app.repositories.sqlite_source_repositories import (
    SQLiteDependencyRepository,
    SQLiteEntityRepository,
    SQLiteExtractionRepository,
    SQLiteMeetingRepository,
    SQLiteMentionRepository,
)
from app.services.background_worker_service import BackgroundJobScheduler, BackgroundWorkerService
from app.services.candidate_scoring_service import CandidateScoringService
from app.services.dependency_resolution_service import DependencyResolutionService
from app.services.entity_service import EntityService
from app.services.extraction_service import ExtractionService
from app.services.meeting_pipeline_orchestrator import MeetingPipelineOrchestrator
from app.services.meeting_processing_service import MeetingProcessingService, ProcessingStage
from app.services.resolution_service import ResolutionService
from app.services.semantic_indexing_service import SemanticIndexingService


class _EvidenceSource:
    def build_semantic_corpus(self, _now):
        return [EvidenceItem(
            evidence_id="worker-evidence", evidence_type=EvidenceType.INSIGHT,
            summary="A durable, evidence-backed issue was processed.",
            source_reference="test-source",
        )]


def _pipeline():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    meetings = InMemoryMeetingRepository()
    extractions = InMemoryExtractionRepository()
    entities = InMemoryEntityRepository()
    mentions = InMemoryMentionRepository()
    dependencies = InMemoryDependencyRepository()
    meetings.save(Meeting(
        meeting_id="m1", title="Delivery", transcript="Alpha depends on Beta.",
        meeting_date=now, ingested_at=now,
    ))
    entity_service = EntityService(entities, mentions)
    alpha, _ = entity_service.create_entity(EntityType.ISSUE, "Alpha")
    entity_service.create_entity(EntityType.ISSUE, "Beta")
    entity_service.register_mention(EntityType.ISSUE, "Alpha", "m1", "Alpha depends on Beta.")
    scoring = CandidateScoringService(mentions, entities, LexicalCandidateGenerator(), LexicalCandidateScorer())
    resolution = ResolutionService(mentions, entities, scoring, ThresholdResolutionPolicy())
    dependency_resolution = DependencyResolutionService(entities, mentions, dependencies)
    semantic_repo = InMemorySemanticIndexRepository()
    semantic = SemanticIndexingService(FakeEmbeddingProvider(), semantic_repo, "fake-embedding-v1", "v1")
    derived_calls = []
    orchestrator = MeetingPipelineOrchestrator(
        extractions, mentions, resolution, dependency_resolution, _EvidenceSource(), semantic,
        derived_services=(lambda entity_id, current_time: derived_calls.append((entity_id, current_time)),),
    )
    extraction = ExtractionService(
        meetings, extractions, FakeExtractionProvider(ExtractionResult(
            meeting_id="m1", extracted_at=now,
            issues=[Issue(description="Alpha is blocked", evidence=Evidence(source_text="Alpha depends on Beta."))],
        )),
    )
    return orchestrator, extraction, extractions, dependencies, semantic_repo, derived_calls, alpha.entity_id


def test_real_services_complete_worker_pipeline_and_persist_outputs():
    orchestrator, extraction, extractions, dependencies, semantic_repo, derived_calls, alpha_id = _pipeline()
    jobs = InMemoryBackgroundJobRepository()
    job = BackgroundJobScheduler(jobs).enqueue(BackgroundJobType.MEETING_PROCESSING, "m1")
    worker = BackgroundWorkerService(jobs, {
        BackgroundJobType.MEETING_PROCESSING: lambda claimed: MeetingProcessingService(jobs, {
            ProcessingStage.EXTRACTED: extraction.extract_meeting,
            ProcessingStage.RESOLVED: orchestrator.resolve_meeting,
            ProcessingStage.RELATIONSHIPS_PERSISTED: orchestrator.persist_relationships,
            ProcessingStage.DERIVED_INTELLIGENCE: orchestrator.derive_intelligence,
            ProcessingStage.SEMANTIC_INDEXED: orchestrator.index_semantic_evidence,
        }).process(claimed)
    })

    result = worker.run_once()

    assert result.status == BackgroundJobStatus.SUCCEEDED
    assert jobs.get(job.job_id).stage == ProcessingStage.COMPLETED
    assert extractions.get_by_meeting_id("m1") is not None
    assert len(dependencies.list_all()) == 1
    assert semantic_repo.count() == 1
    assert derived_calls and derived_calls[0][0] == alpha_id


@pytest.mark.parametrize("failed_stage, expected_stage", [
    (ProcessingStage.EXTRACTED, None),
    (ProcessingStage.RESOLVED, ProcessingStage.EXTRACTED),
    (ProcessingStage.RELATIONSHIPS_PERSISTED, ProcessingStage.RESOLVED),
    (ProcessingStage.DERIVED_INTELLIGENCE, ProcessingStage.RELATIONSHIPS_PERSISTED),
    (ProcessingStage.SEMANTIC_INDEXED, ProcessingStage.DERIVED_INTELLIGENCE),
])
def test_failed_stage_never_creates_a_false_checkpoint(failed_stage, expected_stage):
    jobs = InMemoryBackgroundJobRepository()
    job = BackgroundJobScheduler(jobs).enqueue(BackgroundJobType.MEETING_PROCESSING, "m1")
    calls = []
    handlers = {}
    for stage in ProcessingStage:
        if stage == ProcessingStage.COMPLETED:
            continue
        def run(_meeting_id, stage=stage):
            calls.append(stage)
            if stage == failed_stage:
                raise RuntimeError(stage.value)
        handlers[stage] = run

    worker = BackgroundWorkerService(jobs, {
        BackgroundJobType.MEETING_PROCESSING: lambda claimed: MeetingProcessingService(jobs, handlers).process(claimed)
    })
    result = worker.run_once()

    assert result.status == BackgroundJobStatus.RETRY_WAITING
    assert jobs.get(job.job_id).stage == (expected_stage.value if expected_stage else None)
    assert failed_stage in calls


def test_reprocessing_uses_existing_dependency_and_semantic_upserts():
    orchestrator, extraction, _extractions, dependencies, semantic_repo, _derived_calls, _alpha_id = _pipeline()
    extraction.extract_meeting("m1")
    for _ in range(2):
        orchestrator.resolve_meeting("m1")
        orchestrator.persist_relationships("m1")
        orchestrator.derive_intelligence("m1")
        orchestrator.index_semantic_evidence("m1")

    assert len(dependencies.list_all()) == 1
    assert semantic_repo.count() == 1


def test_sqlite_source_and_persistent_semantic_index_survive_reopen(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = SQLiteSourceStore(tmp_path / "source.db")
    meetings = SQLiteMeetingRepository(store)
    extractions = SQLiteExtractionRepository(store)
    entities = SQLiteEntityRepository(store)
    mentions = SQLiteMentionRepository(store)
    dependencies = SQLiteDependencyRepository(store)
    meetings.save(Meeting(meeting_id="m1", title="Delivery", transcript="Alpha depends on Beta.", meeting_date=now, ingested_at=now))
    entity_service = EntityService(entities, mentions)
    entity_service.create_entity(EntityType.ISSUE, "Alpha")
    entity_service.create_entity(EntityType.ISSUE, "Beta")
    entity_service.register_mention(EntityType.ISSUE, "Alpha", "m1", "Alpha depends on Beta.")
    resolution = ResolutionService(
        mentions, entities,
        CandidateScoringService(mentions, entities, LexicalCandidateGenerator(), LexicalCandidateScorer()),
        ThresholdResolutionPolicy(),
    )
    semantic_path = tmp_path / "semantic.json"
    orchestrator = MeetingPipelineOrchestrator(
        extractions, mentions, resolution,
        DependencyResolutionService(entities, mentions, dependencies), _EvidenceSource(),
        SemanticIndexingService(FakeEmbeddingProvider(), JsonFileSemanticIndexRepository(semantic_path), "fake-embedding-v1", "v1"),
    )
    ExtractionService(meetings, extractions, FakeExtractionProvider(ExtractionResult(meeting_id="m1", extracted_at=now))).extract_meeting("m1")
    orchestrator.resolve_meeting("m1")
    orchestrator.persist_relationships("m1")
    orchestrator.index_semantic_evidence("m1")

    reopened = SQLiteSourceStore(tmp_path / "source.db")
    assert SQLiteExtractionRepository(reopened).get_by_meeting_id("m1") is not None
    assert len(SQLiteDependencyRepository(reopened).list_all()) == 1
    assert JsonFileSemanticIndexRepository(semantic_path).count() == 1
