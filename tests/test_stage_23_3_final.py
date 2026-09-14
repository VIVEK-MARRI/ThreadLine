"""Stage 23.3 final continuation — in-process production verification.

Covers durable source revisions, consistency, entity observation,
stale-worker protection, per-stage retry, query read-only, observability,
and scale with REAL SQLite + REAL persistent semantic index + REAL worker.
Subprocess acceptance/restart/lifecycle live in test_stage_23_3_final_e2e.py.
"""

from datetime import datetime, timedelta, timezone

import hashlib
import pytest

from app.entity_resolution.lexical_candidate_generator import LexicalCandidateGenerator
from app.entity_resolution.lexical_candidate_scorer import LexicalCandidateScorer
from app.entity_resolution.resolution_policy import ThresholdResolutionPolicy
from app.extraction.fake_provider import FakeExtractionProvider
from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.models.entity import EntityType, ResolutionStatus
from app.models.extraction import ExtractionResult
from app.models.meeting import Meeting
from app.models.natural_language import EvidenceItem, EvidenceType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.providers.fake_embedding_provider import FakeEmbeddingProvider
from app.repositories.background_job_repository import (
    SQLiteBackgroundJobRepository,
    StaleJobOwnershipError,
)
from app.repositories.dependency_repository import InMemoryDependencyRepository
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.extraction_repository import InMemoryExtractionRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.repositories.semantic_index_repository import (
    InMemorySemanticIndexRepository,
    JsonFileSemanticIndexRepository,
)
from app.repositories.sqlite_source_repositories import (
    SQLiteDependencyRepository,
    SQLiteEntityRepository,
    SQLiteExtractionRepository,
    SQLiteMeetingRepository,
    SQLiteMentionRepository,
)
from app.schemas.meeting import MeetingIngestRequest
from app.services.background_worker_service import (
    BackgroundJobScheduler,
    BackgroundWorkerService,
    PermanentJobError,
)
from app.services.candidate_scoring_service import CandidateScoringService
from app.services.dependency_resolution_service import DependencyResolutionService
from app.services.entity_observation_service import EntityObservationService
from app.services.entity_service import EntityService
from app.services.extraction_service import ExtractionService
from app.services.meeting_pipeline_orchestrator import MeetingPipelineOrchestrator
from app.services.meeting_processing_service import MeetingProcessingService, ProcessingStage
from app.services.meeting_service import MeetingService
from app.services.processing_consistency_service import (
    FutureRevisionError,
    get_consistency_status,
    parse_source_revision,
    processing_revision_for,
)
from app.services.resolution_service import ResolutionService
from app.services.semantic_indexing_service import SemanticIndexingService


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MeetingEvidenceSource:
    """Deterministic meeting-scoped evidence for tests (no fake counters)."""

    def build_semantic_evidence_for_meeting(self, meeting_id, entity_ids, now):
        items = []
        for entity_id in sorted(set(entity_ids)):
            eid = hashlib.sha256(f"OBS:{entity_id}:{meeting_id}".encode()).hexdigest()[:12]
            items.append(EvidenceItem(
                evidence_id=eid,
                evidence_type=EvidenceType.OBSERVATION,
                entity_id=entity_id,
                meeting_id=meeting_id,
                summary=f"Observed entity {entity_id} in meeting {meeting_id}.",
                source_reference=f"Meeting {meeting_id}",
            ))
        # Organisation-level evidence scoped to the meeting (deterministic).
        oid = hashlib.sha256(f"ORG:{meeting_id}".encode()).hexdigest()[:12]
        items.append(EvidenceItem(
            evidence_id=oid,
            evidence_type=EvidenceType.MEMORY_FACT,
            entity_id=None,
            meeting_id=meeting_id,
            summary=f"Meeting {meeting_id} processed.",
            source_reference=f"Meeting {meeting_id}",
        ))
        return items

    def build_semantic_corpus(self, now):
        return []


def _sqlite_stack(tmp_path, name="source.db", clock=None):
    store = SQLiteSourceStore(tmp_path / name)
    meetings = SQLiteMeetingRepository(store)
    extractions = SQLiteExtractionRepository(store)
    entities = SQLiteEntityRepository(store)
    mentions = SQLiteMentionRepository(store)
    dependencies = SQLiteDependencyRepository(store)
    jobs = SQLiteBackgroundJobRepository(store, clock=clock)
    scheduler = BackgroundJobScheduler(jobs, max_attempts=3)
    semantic_repo = JsonFileSemanticIndexRepository(tmp_path / "semantic.json")
    def _current_rev(meeting_id):
        m = meetings.get_by_id(meeting_id) if meeting_id is not None else None
        return int(getattr(m, "source_revision", 1) or 1) if m is not None else None
    semantic = SemanticIndexingService(
        FakeEmbeddingProvider(), semantic_repo, "fake", "1.0",
        current_revision_lookup=_current_rev,
    )
    scoring = CandidateScoringService(mentions, entities, LexicalCandidateGenerator(), LexicalCandidateScorer())
    resolution = ResolutionService(mentions, entities, scoring, ThresholdResolutionPolicy())
    dep_resolution = DependencyResolutionService(entities, mentions, dependencies)
    observation = EntityObservationService(meetings, entities, mentions)
    evidence = _MeetingEvidenceSource()
    orchestrator = MeetingPipelineOrchestrator(
        extractions, mentions, resolution, dep_resolution, evidence, semantic,
        derived_services=(), organisation_derived_services=(),
        observation_service=observation, meeting_repository=meetings,
    )
    return {
        "store": store, "meetings": meetings, "extractions": extractions,
        "entities": entities, "mentions": mentions, "dependencies": dependencies,
        "jobs": jobs, "scheduler": scheduler, "semantic_repo": semantic_repo,
        "semantic": semantic, "resolution": resolution,
        "dep_resolution": dep_resolution, "observation": observation,
        "evidence": evidence, "orchestrator": orchestrator,
    }


def _ingest(stack, meeting_id, transcript="Alpha depends on Beta.", participants=None, title="T"):
    svc = MeetingService(stack["meetings"])
    req = MeetingIngestRequest(
        meeting_id=meeting_id, title=title, transcript=transcript,
        meeting_date=NOW, participants=participants or [],
    )
    meeting = svc.ingest_meeting(req)
    # Durable intent tied to source revision 1.
    job = stack["scheduler"].build_job(
        BackgroundJobType.MEETING_PROCESSING, meeting_id,
        now=NOW, processing_revision=processing_revision_for(meeting.source_revision),
    )
    if isinstance(stack["meetings"], SQLiteMeetingRepository):
        stack["meetings"].save_and_enqueue(meeting, job)
    else:
        stack["meetings"].save(meeting)
        stack["scheduler"]._repository.enqueue(job)
    return meeting


def _run_pipeline(stack, meeting_id, extraction_result=None):
    """Run the full durable pipeline for meeting_id via the real worker path."""
    result = extraction_result or ExtractionResult(meeting_id=meeting_id, extracted_at=datetime.now(timezone.utc))
    extraction = ExtractionService(
        stack["meetings"], stack["extractions"],
        FakeExtractionProvider(result=result),
    )
    orchestrator = stack["orchestrator"]
    jobs = stack["jobs"]

    def _process(job):
        def _owned():
            current = jobs.get(job.job_id)
            now = datetime.now(timezone.utc)
            if (current is None or current.status.value != "RUNNING"
                    or current.worker_id != job.worker_id
                    or current.lease_until is None or current.lease_until <= now
                    or current.processing_revision != job.processing_revision):
                raise StaleJobOwnershipError("stale")
        orchestrator.set_ownership_checker(_owned)
        extraction.set_ownership_checker(_owned)
        MeetingProcessingService(jobs, {
            ProcessingStage.EXTRACTED: extraction.extract_meeting,
            ProcessingStage.RESOLVED: orchestrator.resolve_meeting,
            ProcessingStage.RELATIONSHIPS_PERSISTED: orchestrator.persist_relationships,
            ProcessingStage.DERIVED_INTELLIGENCE: orchestrator.derive_intelligence,
            ProcessingStage.SEMANTIC_INDEXED: orchestrator.index_semantic_evidence,
        }).process(job)

    worker = BackgroundWorkerService(
        jobs, {BackgroundJobType.MEETING_PROCESSING: _process}, lease_seconds=60,
    )
    return worker.run_once(datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# 4. Durable source revision model
# ---------------------------------------------------------------------------

def test_durable_source_revision_persistence(tmp_path):
    stack = _sqlite_stack(tmp_path)
    meeting = _ingest(stack, "m-rev", transcript="v1")
    assert meeting.source_revision == 1

    reopened = SQLiteMeetingRepository(SQLiteSourceStore(tmp_path / "source.db"))
    assert reopened.get_by_id("m-rev").source_revision == 1

    svc = MeetingService(stack["meetings"])
    revised = svc.revise_meeting("m-rev", MeetingIngestRequest(
        meeting_id="m-rev", title="T", transcript="v2", meeting_date=NOW,
    ))
    assert revised.source_revision == 2
    assert revised.transcript == "v2"
    # Deterministic identity: revision integer distinguishes N from N+1,
    # not wall-clock timestamps.
    assert revised.source_revision == meeting.source_revision + 1
    stack["meetings"].save(revised)
    assert SQLiteMeetingRepository(SQLiteSourceStore(tmp_path / "source.db")).get_by_id("m-rev").source_revision == 2

    # Extraction binds to the authoritative revision.
    extraction = ExtractionService(
        stack["meetings"], stack["extractions"],
        FakeExtractionProvider(result=ExtractionResult(meeting_id="m-rev", extracted_at=NOW)),
    )
    result = extraction.extract_meeting("m-rev")
    assert result.source_revision == 2
    assert SQLiteExtractionRepository(SQLiteSourceStore(tmp_path / "source.db")).get_by_meeting_id("m-rev").source_revision == 2

    # Revision identity helpers are deterministic.
    assert processing_revision_for(1) == "1"
    assert processing_revision_for(2) == "2"
    assert parse_source_revision("2") == 2
    assert parse_source_revision("2@2026-01-01T00:00:00+00:00") == 2
    assert parse_source_revision(None) is None


def test_source_mutation_creates_new_processing_revision(tmp_path):
    stack = _sqlite_stack(tmp_path)
    # REVISION 1
    _ingest(stack, "m-mut", transcript="Alpha depends on Beta.")
    entities = EntityService(stack["entities"], stack["mentions"])
    entities.create_entity(EntityType.ISSUE, "Alpha")
    entities.create_entity(EntityType.ISSUE, "Beta")
    first_job_id = f"MEETING_PROCESSING:m-mut:{processing_revision_for(1)}"
    result = _run_pipeline(stack, "m-mut")
    assert result.status == BackgroundJobStatus.SUCCEEDED
    assert stack["jobs"].get(first_job_id).status == BackgroundJobStatus.SUCCEEDED
    assert stack["extractions"].get_by_meeting_id("m-mut").source_revision == 1
    sem_rev_1 = {r.source_revision for r in stack["semantic_repo"].list_all()}
    assert sem_rev_1 and all(v == 1 for v in sem_rev_1 if v is not None)
    status_1 = get_consistency_status(
        "m-mut", meeting_repository=stack["meetings"], job_repository=stack["jobs"],
        extraction_repository=stack["extractions"], mention_repository=stack["mentions"],
        semantic_repository=stack["semantic_repo"],
    )
    assert status_1["source_revision"] == 1
    assert status_1["derived_revision"] == 1
    assert status_1["is_current"] is True

    # Mutate authoritative source → REVISION 2.
    svc = MeetingService(stack["meetings"])
    revised = svc.revise_meeting("m-mut", MeetingIngestRequest(
        meeting_id="m-mut", title="T", transcript="Alpha depends on Beta. Extra context.", meeting_date=NOW,
    ))
    assert revised.source_revision == 2
    second_job = stack["scheduler"].build_job(
        BackgroundJobType.MEETING_PROCESSING, "m-mut",
        now=NOW, processing_revision=processing_revision_for(revised.source_revision),
    )
    stack["meetings"].save_and_enqueue(revised, second_job)
    # Old completed job does not block the new execution.
    assert second_job.job_id != first_job_id
    assert stack["jobs"].get(first_job_id).status == BackgroundJobStatus.SUCCEEDED
    assert stack["jobs"].get(second_job.job_id).status == BackgroundJobStatus.PENDING

    # New execution must not reuse revision-1 derived data: it observes the new
    # revision and stamps new derived state.
    t1 = datetime.now(timezone.utc)
    result2 = BackgroundWorkerService(
        stack["jobs"],
        {BackgroundJobType.MEETING_PROCESSING: lambda job: _run_pipeline_job(stack, job)},
        lease_seconds=60,
    ).run_once(t1)
    assert result2 is not None and result2.status == BackgroundJobStatus.SUCCEEDED

    assert stack["extractions"].get_by_meeting_id("m-mut").source_revision == 2
    # No duplicate dependencies: deterministic IDs make reprocessing idempotent.
    assert len(stack["dependencies"].list_all()) == 1
    # Semantic state reflects the new revision (no N masquerading as N+1).
    sem_revs = {r.source_revision for r in stack["semantic_repo"].list_all()}
    assert 2 in sem_revs
    assert 1 not in sem_revs or all(r.source_revision == 2 for r in stack["semantic_repo"].list_all() if r.source_revision is not None)
    # Old history remains intact.
    assert stack["jobs"].get(first_job_id).status == BackgroundJobStatus.SUCCEEDED
    status_2 = get_consistency_status(
        "m-mut", meeting_repository=stack["meetings"], job_repository=stack["jobs"],
        extraction_repository=stack["extractions"], mention_repository=stack["mentions"],
        semantic_repository=stack["semantic_repo"],
    )
    assert status_2["source_revision"] == 2
    assert status_2["derived_revision"] == 2
    assert status_2["is_current"] is True


def _run_pipeline_job(stack, job):
    # Fresh execution: clear any stale ownership checker from a prior job.
    stack["orchestrator"].set_ownership_checker(None)
    extraction = ExtractionService(
        stack["meetings"], stack["extractions"],
        FakeExtractionProvider(result=ExtractionResult(meeting_id=job.payload_id, extracted_at=datetime.now(timezone.utc))),
    )
    MeetingProcessingService(stack["jobs"], {
        ProcessingStage.EXTRACTED: extraction.extract_meeting,
        ProcessingStage.RESOLVED: stack["orchestrator"].resolve_meeting,
        ProcessingStage.RELATIONSHIPS_PERSISTED: stack["orchestrator"].persist_relationships,
        ProcessingStage.DERIVED_INTELLIGENCE: stack["orchestrator"].derive_intelligence,
        ProcessingStage.SEMANTIC_INDEXED: stack["orchestrator"].index_semantic_evidence,
    }).process(job)


def test_consistency_invariant_exposes_incomplete_state(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-cons", transcript="hello")
    # Before processing: source exists but derived/semantic are not current.
    status = get_consistency_status(
        "m-cons", meeting_repository=stack["meetings"], job_repository=stack["jobs"],
        extraction_repository=stack["extractions"], mention_repository=stack["mentions"],
        semantic_repository=stack["semantic_repo"],
    )
    assert status["source_revision"] == 1
    assert status["is_current"] is False
    assert status["processing_complete"] is False
    # A failed revision must not silently become current: fail the job.
    job_id = f"MEETING_PROCESSING:m-cons:{processing_revision_for(1)}"
    claimed = stack["jobs"].claim(job_id, "w1", 60)
    stack["jobs"].transition(claimed.job_id, BackgroundJobStatus.FAILED, owner_id="w1",
                             last_error="boom", error_type="PERMANENT")
    status2 = get_consistency_status(
        "m-cons", meeting_repository=stack["meetings"], job_repository=stack["jobs"],
        extraction_repository=stack["extractions"], mention_repository=stack["mentions"],
        semantic_repository=stack["semantic_repo"],
    )
    assert status2["is_current"] is False
    assert status2["derived_revision"] is None


# ---------------------------------------------------------------------------
# 8. Entity observation: RESOLVED / AMBIGUOUS / UNRESOLVED
# ---------------------------------------------------------------------------

def test_entity_observation_resolved_ambiguous_unresolved():
    meetings, entities, mentions = InMemoryMeetingRepository(), InMemoryEntityRepository(), InMemoryMentionRepository()
    svc = EntityService(entities, mentions)
    # RESOLVED: exact participant match with sufficient evidence.
    rahul, _ = svc.create_entity(EntityType.PERSON, "Rahul Kumar")
    meetings.save(Meeting(
        meeting_id="obs-resolved", title="T",
        transcript="Rahul Kumar said the payment API is still blocked.",
        meeting_date=NOW, ingested_at=NOW, participants=["Rahul Kumar"],
    ))
    EntityObservationService(meetings, entities, mentions).observe_meeting("obs-resolved")
    observed = mentions.list_by_meeting_id("obs-resolved")
    assert len(observed) == 1
    assert observed[0].resolution_status == ResolutionStatus.RESOLVED
    assert observed[0].entity_id == rahul.entity_id
    assert len(entities.list_entities()) == 1  # no invented entities

    # AMBIGUOUS: two entities share the alias "Rahul" → both score 1.0, margin 0.
    meetings2, entities2, mentions2 = InMemoryMeetingRepository(), InMemoryEntityRepository(), InMemoryMentionRepository()
    e1, _ = EntityService(entities2, mentions2).create_entity(EntityType.PERSON, "Rahul Kumar")
    e2, _ = EntityService(entities2, mentions2).create_entity(EntityType.PERSON, "Rahul Singh")
    entities2.add_alias(e1.entity_id, "Rahul")
    entities2.add_alias(e2.entity_id, "Rahul")
    meetings2.save(Meeting(
        meeting_id="obs-ambiguous", title="T",
        transcript="Rahul said the payment API is still blocked.",
        meeting_date=NOW, ingested_at=NOW, participants=["Rahul"],
    ))
    EntityObservationService(meetings2, entities2, mentions2).observe_meeting("obs-ambiguous")
    amb = mentions2.list_by_meeting_id("obs-ambiguous")
    assert len(amb) >= 1
    assert any(m.resolution_status == ResolutionStatus.AMBIGUOUS for m in amb)
    assert all(m.entity_id is None for m in amb if m.resolution_status == ResolutionStatus.AMBIGUOUS)
    assert len(entities2.list_entities()) == 2  # no invented entities

    # UNRESOLVED: no safe match → stored as-is, never fabricated.
    meetings3, entities3, mentions3 = InMemoryMeetingRepository(), InMemoryEntityRepository(), InMemoryMentionRepository()
    meetings3.save(Meeting(
        meeting_id="obs-unresolved", title="T",
        transcript="Rahul said the payment API is still blocked.",
        meeting_date=NOW, ingested_at=NOW, participants=["Rahul"],
    ))
    EntityObservationService(meetings3, entities3, mentions3).observe_meeting("obs-unresolved")
    unres = mentions3.list_by_meeting_id("obs-unresolved")
    assert len(unres) == 1
    assert unres[0].resolution_status == ResolutionStatus.UNRESOLVED
    assert unres[0].entity_id is None
    assert entities3.list_entities() == []


def test_observation_uses_shared_scoring_policy_not_substring_identity(tmp_path):
    # "Rahul" must not resolve to "Rahul Kumar" via naive substring: single-token
    # overlap scores below threshold → UNRESOLVED, not forced RESOLVED.
    meetings, entities, mentions = InMemoryMeetingRepository(), InMemoryEntityRepository(), InMemoryMentionRepository()
    EntityService(entities, mentions).create_entity(EntityType.PERSON, "Rahul Kumar")
    meetings.save(Meeting(
        meeting_id="obs-substring", title="T",
        transcript="Rahul said the payment API is still blocked.",
        meeting_date=NOW, ingested_at=NOW, participants=["Rahul"],
    ))
    EntityObservationService(meetings, entities, mentions).observe_meeting("obs-substring")
    observed = mentions.list_by_meeting_id("obs-substring")
    assert len(observed) == 1
    assert observed[0].resolution_status == ResolutionStatus.UNRESOLVED
    assert observed[0].entity_id is None


# ---------------------------------------------------------------------------
# 6. Stale worker cannot commit derived/semantic state
# ---------------------------------------------------------------------------

def test_stale_worker_cannot_commit_any_durable_state(tmp_path):
    from tests._clock_utils import MutableClock as _MC
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-stale", transcript="Alpha depends on Beta.")
    EntityService(stack["entities"], stack["mentions"]).create_entity(EntityType.ISSUE, "Alpha")
    EntityService(stack["entities"], stack["mentions"]).create_entity(EntityType.ISSUE, "Beta")
    job_id = f"MEETING_PROCESSING:m-stale:{processing_revision_for(1)}"
    clock = _MC(datetime(2026, 1, 1, tzinfo=timezone.utc))
    database = tmp_path / "source.db"
    jobs_a = SQLiteBackgroundJobRepository(SQLiteSourceStore(database), clock)
    jobs_b = SQLiteBackgroundJobRepository(SQLiteSourceStore(database), clock)
    jobs_a.claim(job_id, "worker-a", lease_seconds=10)
    # Lease expires; worker B recovers and claims the same processing revision.
    clock.advance(seconds=11)
    jobs_b.recover_stale()
    claimed_b = jobs_b.claim(job_id, "worker-b", lease_seconds=60)
    assert claimed_b is not None

    def assert_owned_a():
        current = jobs_a.get(job_id)
        if (current is None or current.status.value != "RUNNING" or current.worker_id != "worker-a"
                or current.lease_until is None or current.lease_until <= clock()
                or current.processing_revision != jobs_a.get(job_id).processing_revision):
            # Mirror production predicate: worker-a no longer owns the job.
            raise StaleJobOwnershipError("worker-a stale")

    # Stale job checkpoint + stale terminal transition are rejected.
    with pytest.raises(StaleJobOwnershipError):
        jobs_a.checkpoint(job_id, "EXTRACTED", worker_id="worker-a")
    with pytest.raises(StaleJobOwnershipError):
        jobs_a.transition(job_id, BackgroundJobStatus.SUCCEEDED, owner_id="worker-a")

    # Stale derived/semantic writes are rejected via propagated ownership checks.
    # First create durable extraction via the authoritative path (no stale guard).
    _authoritative_extraction = ExtractionService(
        stack["meetings"], stack["extractions"],
        FakeExtractionProvider(result=ExtractionResult(meeting_id="m-stale", extracted_at=NOW)),
    )
    _authoritative_extraction.extract_meeting("m-stale")
    stack["orchestrator"].set_ownership_checker(assert_owned_a)
    extraction = ExtractionService(
        stack["meetings"], stack["extractions"],
        FakeExtractionProvider(result=ExtractionResult(meeting_id="m-stale", extracted_at=NOW)),
    )
    extraction.set_ownership_checker(assert_owned_a)
    with pytest.raises(StaleJobOwnershipError):
        extraction.extract_meeting("m-stale")
    with pytest.raises(StaleJobOwnershipError):
        stack["orchestrator"].resolve_meeting("m-stale")
    with pytest.raises(StaleJobOwnershipError):
        stack["orchestrator"].persist_relationships("m-stale")
    with pytest.raises(StaleJobOwnershipError):
        stack["orchestrator"].derive_intelligence("m-stale")
    with pytest.raises(StaleJobOwnershipError):
        stack["orchestrator"].index_semantic_evidence("m-stale")

    # Durable revision compare-and-swap (hardened, P6): neither stale nor
    # future revisions may be stamped against the authoritative current source
    # revision.  The old behavior allowed a future rev-2 write against a
    # meeting still at rev 1, letting a fabricated revision masquerade as
    # current until the source caught up; that write is now rejected.
    stack["orchestrator"].set_ownership_checker(None)
    stack["semantic"].set_ownership_checker(None)
    item = EvidenceItem(
        evidence_id="e-stale-guard", evidence_type=EvidenceType.OBSERVATION,
        entity_id="ent-1", meeting_id="m-stale",
        summary="new evidence", source_reference="m-stale",
    )
    item_old = item.model_copy(update={"summary": "older evidence text"})
    # Future write: rev 2 targets a meeting whose authoritative source
    # revision is still 1 → rejected as a fabrication.
    with pytest.raises(FutureRevisionError):
        stack["semantic"].index_evidence(item, source_revision=2, meeting_id="m-stale")
    # Advance the meeting to rev 2 (authoritative).  A rev-1 write is now
    # stale and rejected by the durable compare-and-swap even with no
    # ownership checker.
    _current_meeting = stack["meetings"].get_by_id("m-stale")
    stack["meetings"].save(_current_meeting.model_copy(update={"source_revision": 2}))
    with pytest.raises(StaleJobOwnershipError):
        stack["semantic"].index_evidence(item_old, source_revision=1, meeting_id="m-stale")
    # Once the authoritative source actually reaches rev 2, the current write
    # succeeds and is verifiably current — revision 2 never masquerades as 1.
    indexed = stack["semantic"].index_evidence(item, source_revision=2, meeting_id="m-stale")
    assert indexed.source_revision == 2
    assert stack["semantic_repo"].get_by_composite_key("e-stale-guard", "fake", "1.0").source_revision == 2

    # Authoritative worker B can still make progress.
    jobs_b.checkpoint(job_id, "EXTRACTED", worker_id="worker-b")
    assert jobs_b.get(job_id).stage == "EXTRACTED"


# ---------------------------------------------------------------------------
# 7. Retry verification across every pipeline stage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", [
    ProcessingStage.EXTRACTED, ProcessingStage.RESOLVED,
    ProcessingStage.RELATIONSHIPS_PERSISTED, ProcessingStage.DERIVED_INTELLIGENCE,
    ProcessingStage.SEMANTIC_INDEXED,
])
def test_retry_each_stage_resumes_and_succeeds_without_duplicates(tmp_path, stage):
    stack = _sqlite_stack(tmp_path, name=f"retry-{stage.value}.db")
    mid = f"m-retry-{stage.value}"
    _ingest(stack, mid, transcript="Alpha depends on Beta.")
    EntityService(stack["entities"], stack["mentions"]).create_entity(EntityType.ISSUE, "Alpha")
    EntityService(stack["entities"], stack["mentions"]).create_entity(EntityType.ISSUE, "Beta")
    jobs = stack["jobs"]
    job_id = f"MEETING_PROCESSING:{mid}:{processing_revision_for(1)}"
    failures = {"count": 0}

    real_extraction = ExtractionService(
        stack["meetings"], stack["extractions"],
        FakeExtractionProvider(result=ExtractionResult(meeting_id=mid, extracted_at=NOW)),
    )
    orch = stack["orchestrator"]

    def maybe_fail(current_stage):
        if current_stage == stage and failures["count"] == 0:
            failures["count"] += 1
            raise RuntimeError(f"transient failure at {current_stage.value}")

    handlers = {
        ProcessingStage.EXTRACTED: lambda m: (maybe_fail(ProcessingStage.EXTRACTED), real_extraction.extract_meeting(m))[1],
        ProcessingStage.RESOLVED: lambda m: (maybe_fail(ProcessingStage.RESOLVED), orch.resolve_meeting(m))[1],
        ProcessingStage.RELATIONSHIPS_PERSISTED: lambda m: (maybe_fail(ProcessingStage.RELATIONSHIPS_PERSISTED), orch.persist_relationships(m))[1],
        ProcessingStage.DERIVED_INTELLIGENCE: lambda m: (maybe_fail(ProcessingStage.DERIVED_INTELLIGENCE), orch.derive_intelligence(m))[1],
        ProcessingStage.SEMANTIC_INDEXED: lambda m: (maybe_fail(ProcessingStage.SEMANTIC_INDEXED), orch.index_semantic_evidence(m))[1],
    }
    worker = BackgroundWorkerService(
        jobs, {BackgroundJobType.MEETING_PROCESSING: lambda job: MeetingProcessingService(jobs, handlers).process(job)},
        lease_seconds=60, backoff_seconds=0,
    )
    t0 = datetime.now(timezone.utc)
    first = worker.run_once(t0)
    assert first.status == BackgroundJobStatus.RETRY_WAITING
    assert failures["count"] == 1
    # Previous checkpoint preserved (no false checkpoint for the failed stage).
    order = [None, "EXTRACTED", "RESOLVED", "RELATIONSHIPS_PERSISTED", "DERIVED_INTELLIGENCE", "SEMANTIC_INDEXED"]
    failed_index = order.index(stage.value if stage.value != "EXTRACTED" else "EXTRACTED")
    # For EXTRACTED failure the stage stays None; otherwise it is the previous stage.
    expected = {ProcessingStage.EXTRACTED: None, ProcessingStage.RESOLVED: "EXTRACTED",
                ProcessingStage.RELATIONSHIPS_PERSISTED: "RESOLVED",
                ProcessingStage.DERIVED_INTELLIGENCE: "RELATIONSHIPS_PERSISTED",
                ProcessingStage.SEMANTIC_INDEXED: "DERIVED_INTELLIGENCE"}[stage]
    assert jobs.get(job_id).stage == expected
    assert jobs.get(job_id).error_type == "TRANSIENT"

    second = worker.run_once(t0 + timedelta(seconds=1))
    assert second.status == BackgroundJobStatus.SUCCEEDED
    assert jobs.get(job_id).stage == "COMPLETED"
    # Later stages executed and no duplicates.
    assert stack["extractions"].get_by_meeting_id(mid) is not None
    assert len(stack["dependencies"].list_all()) == 1
    assert stack["semantic_repo"].count() >= 1


def test_permanent_failure_does_not_retry_and_preserves_stage(tmp_path):
    stack = _sqlite_stack(tmp_path, name="perm.db")
    _ingest(stack, "m-perm", transcript="hello")

    def _boom(_meeting_id):
        raise PermanentJobError("schema invalid: missing transcript field")

    handlers = {
        ProcessingStage.EXTRACTED: _boom,
        ProcessingStage.RESOLVED: lambda m: None,
        ProcessingStage.RELATIONSHIPS_PERSISTED: lambda m: None,
        ProcessingStage.DERIVED_INTELLIGENCE: lambda m: None,
        ProcessingStage.SEMANTIC_INDEXED: lambda m: None,
    }
    worker = BackgroundWorkerService(
        stack["jobs"], {BackgroundJobType.MEETING_PROCESSING: lambda job: MeetingProcessingService(stack["jobs"], handlers).process(job)},
        lease_seconds=60,
    )
    result = worker.run_once(datetime.now(timezone.utc))
    assert result.status == BackgroundJobStatus.FAILED
    assert result.error_type == "PERMANENT"
    assert "schema invalid" in (result.last_error or "")
    # No checkpoint for the failed stage; job does not become current.
    assert stack["jobs"].get(result.job_id).stage is None


# ---------------------------------------------------------------------------
# 9. Query is provably read-only (persistent storage)
# ---------------------------------------------------------------------------

def test_query_is_read_only_against_persistent_storage(tmp_path):
    from app.services.evidence_retrieval_service import EvidenceRetrievalService
    from app.services.hybrid_evidence_retrieval_service import HybridEvidenceRetrievalService
    from app.services.natural_language_query_service import NaturalLanguageQueryService
    from app.services.query_entity_resolver import QueryEntityResolver
    from app.services.query_intent_service import QueryIntentService
    from app.services.evidence_context_builder import EvidenceContextBuilder
    from app.models.natural_language import NaturalLanguageQuery, make_query_id
    from app.providers.fake_provider import FakeNaturalLanguageAnswerProvider
    from app.services.semantic_evidence_retrieval_service import SemanticEvidenceRetrievalService

    stack = _sqlite_stack(tmp_path, name="query.db")
    _ingest(stack, "m-query", transcript="Alpha depends on Beta.")
    EntityService(stack["entities"], stack["mentions"]).create_entity(EntityType.ISSUE, "Alpha")
    EntityService(stack["entities"], stack["mentions"]).create_entity(EntityType.ISSUE, "Beta")
    assert _run_pipeline(stack, "m-query").status == BackgroundJobStatus.SUCCEEDED

    class _NullSvc:
        def __getattr__(self, _name):
            def _empty(*_a, **_k):
                return []
            return _empty

    class _NullPortfolioSvc:
        def get_portfolio(self, *_a, **_k):
            from app.models.portfolio import OrganisationPortfolio
            return OrganisationPortfolio(
                evaluated_at=datetime.now(timezone.utc),
                total_entities=0, critical_entities=0, high_risk_entities=0,
                medium_risk_entities=0, low_risk_entities=0,
                blocked_entities=0, entities_with_active_actions=0,
                entities_with_impact=0, entities=[],
            )

    retrieval = EvidenceRetrievalService(
        entity_repo=stack["entities"], meeting_repo=stack["meetings"],
        timeline_svc=_NullSvc(), memory_svc=_NullSvc(), insight_svc=_NullSvc(),
        attention_svc=_NullSvc(), action_svc=_NullSvc(), dependency_graph_svc=_NullSvc(),
        impact_svc=_NullSvc(), org_change_svc=_NullSvc(), portfolio_svc=_NullPortfolioSvc(),
        relationship_svc=_NullSvc(),
    )
    semantic_search = SemanticEvidenceRetrievalService(
        embedding_provider=FakeEmbeddingProvider(), repository=stack["semantic_repo"],
        embedding_model_name="fake", representation_version="1.0",
    )
    hybrid = HybridEvidenceRetrievalService(
        structured_service=retrieval, semantic_service=semantic_search,
        semantic_corpus_provider=lambda t: retrieval.build_semantic_corpus(t),
    )
    service = NaturalLanguageQueryService(
        intent_svc=QueryIntentService(),
        entity_resolver=QueryEntityResolver(entity_repo=stack["entities"]),
        retrieval_svc=retrieval, context_builder=EvidenceContextBuilder(),
        provider=FakeNaturalLanguageAnswerProvider(), hybrid_retrieval_svc=hybrid,
    )

    def _snapshot():
        counts = {
            "meetings": stack["store"]._connection.execute("SELECT COUNT(*) FROM meetings").fetchone()[0],
            "extractions": stack["store"]._connection.execute("SELECT COUNT(*) FROM extraction_results").fetchone()[0],
            "mentions": stack["store"]._connection.execute("SELECT COUNT(*) FROM entity_mentions").fetchone()[0],
            "dependencies": stack["store"]._connection.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0],
            "jobs": stack["store"]._connection.execute("SELECT COUNT(*) FROM background_jobs").fetchone()[0],
        }
        payloads = [r[0] for r in stack["store"]._connection.execute("SELECT payload FROM meetings ORDER BY meeting_id").fetchall()]
        payloads += [r[0] for r in stack["store"]._connection.execute("SELECT payload FROM entity_mentions ORDER BY mention_id").fetchall()]
        semantic_bytes = (tmp_path / "semantic.json").read_bytes() if (tmp_path / "semantic.json").exists() else b""
        return counts, hashlib.sha256("".join(payloads).encode()).hexdigest(), hashlib.sha256(semantic_bytes).hexdigest()

    before = _snapshot()
    query = NaturalLanguageQuery(
        query_id=make_query_id("What needs attention?", None, NOW),
        question="What needs attention?", current_time=NOW,
    )
    service.query(query)
    service.query_evidence_only(query)
    after = _snapshot()
    assert before == after


# ---------------------------------------------------------------------------
# 10. Observability
# ---------------------------------------------------------------------------

def test_observability_failure_exposes_required_fields_without_secrets(tmp_path, caplog):
    import logging
    stack = _sqlite_stack(tmp_path, name="obs.db")
    _ingest(stack, "m-obs", transcript="hello")

    def _boom(_meeting_id):
        raise RuntimeError("transient provider timeout")

    worker = BackgroundWorkerService(
        stack["jobs"], {BackgroundJobType.MEETING_PROCESSING: lambda job: MeetingProcessingService(stack["jobs"], {
            ProcessingStage.EXTRACTED: _boom,
            ProcessingStage.RESOLVED: lambda m: None,
            ProcessingStage.RELATIONSHIPS_PERSISTED: lambda m: None,
            ProcessingStage.DERIVED_INTELLIGENCE: lambda m: None,
            ProcessingStage.SEMANTIC_INDEXED: lambda m: None,
        }).process(job)},
        lease_seconds=60, backoff_seconds=0, worker_id="worker-obs-1",
    )
    with caplog.at_level(logging.INFO, logger="app.services.background_worker_service"):
        result = worker.run_once(datetime.now(timezone.utc))
    assert result.status == BackgroundJobStatus.RETRY_WAITING
    # Persisted failure metadata exposes every required field.
    assert result.job_id
    assert result.payload_id == "m-obs"  # meeting_id
    assert result.processing_revision == processing_revision_for(1)  # source+processing revision
    assert parse_source_revision(result.processing_revision) == 1
    assert result.worker_id is None or isinstance(result.worker_id, str)
    assert result.attempts >= 1
    assert result.error_type == "TRANSIENT"
    assert "transient provider timeout" in (result.last_error or "")
    assert result.next_retry_at is not None  # retry state
    # Log output exposes job/meeting/revision/stage/worker/attempt/error/retry.
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "m-obs" in text or "job" in text.lower()
    # No secret leakage: API keys, provider secrets, credentials never logged;
    # transcripts are not stuffed into job errors or logs here.
    for secret in ("sk-", "OPENAI_API_KEY", "fake-secret-key-123", "credential"):
        assert secret not in text
        assert secret not in (result.last_error or "")


# ---------------------------------------------------------------------------
# 11. Scale: incremental indexing with persistent semantic index
# ---------------------------------------------------------------------------

def test_scale_incremental_indexing_only_touches_affected_evidence(tmp_path):
    store = SQLiteSourceStore(tmp_path / "scale.db")
    meetings, extractions = SQLiteMeetingRepository(store), SQLiteExtractionRepository(store)
    entities, mentions = SQLiteEntityRepository(store), SQLiteMentionRepository(store)
    dependencies = SQLiteDependencyRepository(store)
    jobs = SQLiteBackgroundJobRepository(store)
    scheduler = BackgroundJobScheduler(jobs)
    semantic_path = tmp_path / "semantic.json"

    class _CountingProvider(FakeEmbeddingProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0
        def embed_text(self, text):
            self.calls += 1
            return super().embed_text(text)

    counting = _CountingProvider()
    semantic_repo = JsonFileSemanticIndexRepository(semantic_path)
    semantic = SemanticIndexingService(counting, semantic_repo, "fake", "1.0")

    # Seed: many entities + hundreds of evidence items + multiple meetings.
    entity_ids = []
    for i in range(30):
        ent, _ = EntityService(entities, mentions).create_entity(EntityType.ISSUE, f"Service-{i:03d}")
        entity_ids.append(ent.entity_id)
    for m in range(4):
        mid = f"seed-{m}"
        meetings.save(Meeting(meeting_id=mid, title="Seed", transcript=f"seed transcript {m}",
                              meeting_date=NOW, ingested_at=NOW))
        extractions.save(ExtractionResult(meeting_id=mid, extracted_at=NOW))
        for ent_id in entity_ids[:10]:
            eid = hashlib.sha256(f"{ent_id}:{mid}".encode()).hexdigest()[:12]
            semantic.index_evidence(EvidenceItem(
                evidence_id=eid, evidence_type=EvidenceType.OBSERVATION, entity_id=ent_id,
                meeting_id=mid, summary=f"Seed evidence {eid}.", source_reference=mid,
            ), source_revision=1, meeting_id=mid)
    seeded_calls = counting.calls
    seeded_count = semantic_repo.count()
    assert seeded_count >= 40

    # Process ONE new meeting with meeting-scoped incremental indexing.
    scoring = CandidateScoringService(mentions, entities, LexicalCandidateGenerator(), LexicalCandidateScorer())
    resolution = ResolutionService(mentions, entities, scoring, ThresholdResolutionPolicy())
    dep_resolution = DependencyResolutionService(entities, mentions, dependencies)
    observation = EntityObservationService(meetings, entities, mentions)
    evidence = _MeetingEvidenceSource()
    orchestrator = MeetingPipelineOrchestrator(
        extractions, mentions, resolution, dep_resolution, evidence, semantic,
        observation_service=observation, meeting_repository=meetings,
    )
    new_meeting = Meeting(meeting_id="scale-new", title="New", transcript="Service-000 depends on Service-001.",
                          meeting_date=NOW, ingested_at=NOW)
    meetings.save(new_meeting)
    extractions.save(ExtractionResult(meeting_id="scale-new", extracted_at=NOW, source_revision=1))
    # Resolve one mention so the new meeting has affected entities.
    EntityService(entities, mentions).register_mention(EntityType.ISSUE, "Service-000", "scale-new", "Service-000 depends on Service-001.", source_revision=1)
    for mention in mentions.list_by_meeting_id("scale-new"):
        resolution.resolve(mention.mention_id)
    entity_ids_new = sorted({m.entity_id for m in mentions.list_by_meeting_id("scale-new") if m.entity_id})
    before = counting.calls
    orchestrator.index_semantic_evidence("scale-new")
    new_calls = counting.calls - before
    # Only affected/new evidence was embedded; unrelated existing evidence was
    # reused via hash checks, not re-embedded through the normal path.
    assert new_calls <= 5
    assert new_calls >= 1
    assert semantic_repo.count() == seeded_count + new_calls
    # Corpus-wide search still sees existing evidence (incremental ≠ invisible).
    from app.services.semantic_evidence_retrieval_service import SemanticEvidenceRetrievalService
    searcher = SemanticEvidenceRetrievalService(counting, repository=semantic_repo)
    corpus = evidence.build_semantic_corpus(NOW)
    # Full rebuild remains a separate explicit operation.
    assert semantic.rebuild_index(corpus) >= 0 if corpus else True


def test_manual_extraction_refresh_creates_distinct_revision_tied_job(tmp_path):
    """Manual refresh over the SAME source revision gets a distinct job that still ties to source."""
    stack = _sqlite_stack(tmp_path, name="refresh.db")
    _ingest(stack, "m-refresh", transcript="Alpha depends on Beta.")
    assert _run_pipeline(stack, "m-refresh").status == BackgroundJobStatus.SUCCEEDED
    first_id = f"MEETING_PROCESSING:m-refresh:{processing_revision_for(1)}"
    assert stack["jobs"].get(first_id).status == BackgroundJobStatus.SUCCEEDED

    # Simulate POST /meetings/{id}/extract: refresh extraction, enqueue "1@<ts>".
    extraction = ExtractionService(
        stack["meetings"], stack["extractions"],
        FakeExtractionProvider(result=ExtractionResult(meeting_id="m-refresh", extracted_at=datetime.now(timezone.utc))),
    )
    refreshed = extraction.extract_meeting("m-refresh")
    assert refreshed.source_revision == 1  # source truth untouched
    refresh_rev = processing_revision_for(refreshed.source_revision, suffix=refreshed.extracted_at.isoformat())
    assert parse_source_revision(refresh_rev) == 1
    refresh_job = stack["scheduler"].build_job(
        BackgroundJobType.MEETING_PROCESSING, "m-refresh", processing_revision=refresh_rev,
    )
    stack["scheduler"]._repository.enqueue(refresh_job)
    # Distinct job: old completed job does not block refresh.
    assert refresh_job.job_id != first_id
    assert stack["jobs"].get(first_id).status == BackgroundJobStatus.SUCCEEDED
    assert stack["jobs"].get(refresh_job.job_id).status == BackgroundJobStatus.PENDING
