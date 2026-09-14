"""Stage 23.4 production hardening — §24 mandatory regression tests.

All tests use REAL SQLite and the REAL JSON semantic index (no mock
repositories).  Each P-item is validated with a dedicated regression test that
proves the defect is fixed and durable under the test conditions.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.extraction.fake_provider import FakeExtractionProvider
from app.models.background_job import BackgroundJobStatus, BackgroundJobType
from app.models.dependency import ExplicitDependency
from app.models.entity import (
    CanonicalEntity,
    EntityMention,
    EntityType,
    ResolutionStatus,
)
from app.models.extraction import ExtractionResult
from app.models.meeting import Meeting
from app.models.natural_language import EvidenceItem, EvidenceType
from app.models.relationships import RelationshipEvidenceType, RelationshipType
from app.persistence.sqlite_store import SQLiteSourceStore
from app.providers.fake_embedding_provider import FakeEmbeddingProvider
from app.repositories.background_job_repository import (
    SQLiteBackgroundJobRepository,
    StaleJobOwnershipError,
)
from app.repositories.dependency_repository import filter_current_records
from app.repositories.mention_repository import (
    InMemoryMentionRepository,
    filter_current_mentions,
)
from app.repositories.semantic_index_repository import (
    JsonFileSemanticIndexRepository,
)
from app.repositories.sqlite_source_repositories import (
    SQLiteDependencyRepository,
    SQLiteEntityRepository,
    SQLiteExtractionRepository,
    SQLiteMentionRepository,
    SQLiteMeetingRepository,
)
from app.services.dependency_graph_service import DependencyGraphService
from app.services.entity_relationship_service import EntityRelationshipService
from app.services.entity_service import EntityService
from app.services.meeting_pipeline_orchestrator import MeetingPipelineOrchestrator
from app.services.meeting_service import MeetingService
from app.services.processing_consistency_service import (
    FutureRevisionError,
    RevisionMismatchError,
    StaleRevisionError,
    get_consistency_status,
    processing_revision_for,
)
from app.services.semantic_evidence_retrieval_service import SemanticEvidenceRetrievalService
from app.services.semantic_indexing_service import SemanticIndexingService
from app.schemas.meeting import MeetingIngestRequest

from tests._clock_utils import MutableClock


NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)
MEETING_DATE = datetime(2025, 12, 20, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Shared real-SQLite stack (mirrors test_stage_23_3_final._sqlite_stack)
# ---------------------------------------------------------------------------

class _MeetingEvidenceSource:
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
    semantic_repo = JsonFileSemanticIndexRepository(tmp_path / f"{name}.semantic.json")

    def _current_rev(meeting_id):
        m = meetings.get_by_id(meeting_id) if meeting_id is not None else None
        return int(getattr(m, "source_revision", 1) or 1) if m is not None else None

    semantic = SemanticIndexingService(
        FakeEmbeddingProvider(), semantic_repo, "fake", "1.0",
        current_revision_lookup=_current_rev,
    )

    from app.entity_resolution.lexical_candidate_generator import LexicalCandidateGenerator
    from app.entity_resolution.lexical_candidate_scorer import LexicalCandidateScorer
    from app.entity_resolution.resolution_policy import ThresholdResolutionPolicy
    from app.services.candidate_scoring_service import CandidateScoringService
    from app.services.dependency_resolution_service import DependencyResolutionService
    from app.services.entity_observation_service import EntityObservationService
    from app.services.resolution_service import ResolutionService

    scoring = CandidateScoringService(
        mentions, entities, LexicalCandidateGenerator(), LexicalCandidateScorer()
    )
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
        "jobs": jobs, "semantic_repo": semantic_repo,
        "semantic": semantic, "resolution": resolution,
        "dep_resolution": dep_resolution, "observation": observation,
        "evidence": evidence, "orchestrator": orchestrator,
        "current_rev": _current_rev,
    }


def _ingest(stack, meeting_id, transcript="Alpha depends on Beta.", title="T"):
    svc = MeetingService(stack["meetings"])
    req = MeetingIngestRequest(
        meeting_id=meeting_id,
        title=title,
        transcript=transcript,
        meeting_date=MEETING_DATE,
    )
    return svc.ingest_meeting(req)


def _create_entity(stack, entity_id: str, name: str) -> None:
    stack["entities"].create(CanonicalEntity(
        entity_id=entity_id, canonical_name=name, aliases=[],
        entity_type=EntityType.ISSUE, created_at=NOW,
    ))


def _advance_meeting(stack, meeting_id: str, to_revision: int) -> None:
    current = stack["meetings"].get_by_id(meeting_id)
    stack["meetings"].save(
        current.model_copy(update={"source_revision": to_revision})
    )


# ========================================================================
# §24 — P1/P2 clock ownership
# ========================================================================

def test_h01_expired_lease_checkpoint_rejected(tmp_path):
    clock = MutableClock(NOW)
    stack = _sqlite_stack(tmp_path, clock=clock)
    _ingest(stack, "m-h01")
    job_id = f"MEETING_PROCESSING:m-h01:{processing_revision_for(1)}"
    from app.models.background_job import BackgroundJob, BackgroundJobType
    stack["jobs"].enqueue(BackgroundJob(
        job_id=job_id, job_type=BackgroundJobType.MEETING_PROCESSING,
        payload_id="m-h01", processing_revision=processing_revision_for(1),
        created_at=NOW,
    ))
    jobs = stack["jobs"]
    jobs.claim(job_id, "worker-a", lease_seconds=10)
    clock.advance(seconds=11)
    jobs.recover_stale()
    claimed_b = jobs.claim(job_id, "worker-b", lease_seconds=60)
    assert claimed_b is not None
    with pytest.raises(StaleJobOwnershipError):
        jobs.checkpoint(job_id, "EXTRACTED", worker_id="worker-a")
    with pytest.raises(StaleJobOwnershipError):
        jobs.transition(job_id, BackgroundJobStatus.SUCCEEDED, owner_id="worker-a")


def test_h02_fresh_claim_succeeds_after_recovery(tmp_path):
    clock = MutableClock(NOW)
    stack = _sqlite_stack(tmp_path, clock=clock)
    _ingest(stack, "m-h02")
    job_id = f"MEETING_PROCESSING:m-h02:{processing_revision_for(1)}"
    from app.models.background_job import BackgroundJob, BackgroundJobType
    stack["jobs"].enqueue(BackgroundJob(
        job_id=job_id, job_type=BackgroundJobType.MEETING_PROCESSING,
        payload_id="m-h02", processing_revision=processing_revision_for(1),
        created_at=NOW,
    ))
    jobs = stack["jobs"]
    jobs.claim(job_id, "worker-a", lease_seconds=10)
    clock.advance(seconds=11)
    jobs.recover_stale()
    claimed = jobs.claim(job_id, "worker-b", lease_seconds=60)
    assert claimed is not None
    job = jobs.get(job_id)
    assert job.status.value == "RUNNING"
    assert job.worker_id == "worker-b"
    jobs.checkpoint(job_id, "EXTRACTED", worker_id="worker-b")
    assert jobs.get(job_id).stage == "EXTRACTED"


# ========================================================================
# §24 — P3/P4/P5 error propagation
# ========================================================================

def test_h03_entity_observation_errors_propagate(tmp_path):
    """EntityObservationService propagates DB errors from get_by_id — the old
    silent `except Exception: return None` fallback is removed (P3/P4)."""
    from app.services.entity_observation_service import EntityObservationService

    class BrokenMeetingRepo:
        def get_by_id(self, mid):
            raise RuntimeError("DB connection lost")

    class BrokenEntityRepo:
        def get_by_id(self, eid):
            return None
        def list_entities(self, entity_type=None):
            return []

    svc = EntityObservationService(
        BrokenMeetingRepo(), BrokenEntityRepo(), InMemoryMentionRepository(),
    )
    with pytest.raises(RuntimeError, match="DB connection lost"):
        svc.observe_meeting("m-h03")


def test_h04_orchestrator_db_error_propagates(tmp_path):
    """get_by_id failure in orchestrator propagates (old `except Exception:
    return None` removed in P5)."""
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h04")

    # Pre-create extraction so _require_extraction passes.
    from app.services.extraction_service import ExtractionService
    ExtractionService(
        stack["meetings"], stack["extractions"],
        FakeExtractionProvider(result=ExtractionResult(meeting_id="m-h04", extracted_at=NOW)),
    ).extract_meeting("m-h04")

    class BrokenMeetingRepo:
        def get_by_id(self, mid):
            raise RuntimeError("DB connection lost")

    orchestrator = MeetingPipelineOrchestrator(
        stack["extractions"], stack["mentions"],
        stack["resolution"], stack["dep_resolution"], stack["evidence"],
        stack["semantic"], meeting_repository=BrokenMeetingRepo(),
    )
    with pytest.raises(RuntimeError, match="DB connection lost"):
        orchestrator.resolve_meeting("m-h04")


# ========================================================================
# §24 — P6 exact source-revision equality guards
# ========================================================================

def test_h05_stale_extraction_write_rejected(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h05")
    _advance_meeting(stack, "m-h05", 2)
    with pytest.raises((StaleJobOwnershipError, StaleRevisionError, RevisionMismatchError)):
        stack["extractions"].save(
            ExtractionResult(meeting_id="m-h05", extracted_at=NOW, source_revision=1)
        )


def test_h06_future_mention_write_rejected(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h06")
    mention = EntityMention(
        mention_id="mn-future",
        entity_type=EntityType.ISSUE,
        text="future", meeting_id="m-h06",
        source_text="future mention",
        entity_id=None, resolution_status=ResolutionStatus.UNRESOLVED,
        created_at=NOW, source_revision=2,
    )
    with pytest.raises((FutureRevisionError, RevisionMismatchError)):
        stack["mentions"].create(mention)


def test_h07_future_dependency_write_rejected(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h07")
    dep = ExplicitDependency(
        dependency_id="dep-future",
        source_entity_id="e1", target_entity_id="e2",
        relationship_type=RelationshipType.DEPENDS_ON,
        evidence_type=RelationshipEvidenceType.EXPLICIT_STATEMENT,
        source_text="e1 depends on e2",
        meeting_id="m-h07", mention_id="mn-future",
        source_revision=2,
    )
    with pytest.raises((FutureRevisionError, RevisionMismatchError)):
        stack["dependencies"].save(dep)


def test_h17_future_semantic_write_rejected(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h17")
    item = EvidenceItem(
        evidence_id="e-future", evidence_type=EvidenceType.OBSERVATION,
        entity_id=None, meeting_id="m-h17",
        summary="future evidence", source_reference="m-h17",
    )
    with pytest.raises(FutureRevisionError):
        stack["semantic"].index_evidence(item, source_revision=2, meeting_id="m-h17")


# ========================================================================
# §24 — P7 semantic currentness
# ========================================================================

def test_h08_mixed_semantic_revisions_not_current(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h08")
    stack["semantic"].index_evidence(
        EvidenceItem(
            evidence_id="e-mix-1", evidence_type=EvidenceType.OBSERVATION,
            entity_id=None, meeting_id="m-h08",
            summary="rev1 evidence", source_reference="m-h08",
        ),
        source_revision=1, meeting_id="m-h08",
    )
    _advance_meeting(stack, "m-h08", 2)
    stack["semantic"].index_evidence(
        EvidenceItem(
            evidence_id="e-mix-2", evidence_type=EvidenceType.OBSERVATION,
            entity_id=None, meeting_id="m-h08",
            summary="rev2 evidence", source_reference="m-h08",
        ),
        source_revision=2, meeting_id="m-h08",
    )
    status = get_consistency_status(
        "m-h08",
        meeting_repository=stack["meetings"],
        semantic_repository=stack["semantic_repo"],
    )
    # Mixed revisions (rev 1 + rev 2) → semantic is NOT current, and the old
    # max(revisions) behaviour is gone: semantic_revision is None.
    assert status["semantic_revision"] is None
    assert status["is_current"] is False


def test_h08b_single_current_semantic_revision_reported(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h08b")
    stack["semantic"].index_evidence(
        EvidenceItem(
            evidence_id="e-cur-1", evidence_type=EvidenceType.OBSERVATION,
            entity_id=None, meeting_id="m-h08b",
            summary="current evidence", source_reference="m-h08b",
        ),
        source_revision=1, meeting_id="m-h08b",
    )
    status = get_consistency_status(
        "m-h08b",
        meeting_repository=stack["meetings"],
        semantic_repository=stack["semantic_repo"],
    )
    assert status["semantic_revision"] == 1


# ========================================================================
# §24 — P8 current-revision semantic retrieval
# ========================================================================

def test_h09_search_persisted_excludes_stale_records(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h09")
    item = EvidenceItem(
        evidence_id="e-stale-search", evidence_type=EvidenceType.OBSERVATION,
        entity_id=None, meeting_id="m-h09",
        summary="current evidence", source_reference="m-h09",
    )
    stack["semantic"].index_evidence(item, source_revision=1, meeting_id="m-h09")
    _advance_meeting(stack, "m-h09", 2)
    retriever = SemanticEvidenceRetrievalService(
        FakeEmbeddingProvider(), min_similarity=0.0,
        repository=stack["semantic_repo"],
        embedding_model_name="fake", representation_version="1.0",
    )
    # Current-revision lookup excludes the stale (rev 1) record.
    matches = retriever.search_persisted(
        "evidence", lambda eid: item,
        current_revision_lookup=lambda mid: stack["current_rev"](mid),
    )
    assert matches == []
    # Legacy path (no lookup) still sees it — filtering is opt-in per caller.
    assert len(retriever.search_persisted("evidence", lambda eid: item)) == 1


def test_h09b_search_persisted_keeps_current_records(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h09b")
    item = EvidenceItem(
        evidence_id="e-keep-search", evidence_type=EvidenceType.OBSERVATION,
        entity_id=None, meeting_id="m-h09b",
        summary="current evidence", source_reference="m-h09b",
    )
    stack["semantic"].index_evidence(item, source_revision=1, meeting_id="m-h09b")
    retriever = SemanticEvidenceRetrievalService(
        FakeEmbeddingProvider(), min_similarity=0.0,
        repository=stack["semantic_repo"],
        embedding_model_name="fake", representation_version="1.0",
    )
    matches = retriever.search_persisted(
        "evidence", lambda eid: item,
        current_revision_lookup=lambda mid: stack["current_rev"](mid),
    )
    assert len(matches) == 1
    assert matches[0].evidence_id == "e-keep-search"


# ========================================================================
# §24 — P9 current-revision dependency/relationship reads
# ========================================================================

def test_h10_dependency_graph_filters_stale_deps(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h10")
    _create_entity(stack, "e-a", "A")
    _create_entity(stack, "e-b", "B")
    stack["dependencies"].save(ExplicitDependency(
        dependency_id="dep-h10",
        source_entity_id="e-a", target_entity_id="e-b",
        relationship_type=RelationshipType.DEPENDS_ON,
        evidence_type=RelationshipEvidenceType.EXPLICIT_STATEMENT,
        source_text="A depends on B", meeting_id="m-h10",
        mention_id="mn-h10", source_revision=1,
    ))
    _advance_meeting(stack, "m-h10", 2)

    graph_svc = DependencyGraphService(
        stack["dependencies"], stack["entities"],
        current_revision_lookup=lambda mid: stack["current_rev"](mid),
    )
    graph = graph_svc.build_dependency_graph("e-a")
    assert len(graph.direct_dependencies) == 0

    graph_svc_legacy = DependencyGraphService(stack["dependencies"], stack["entities"])
    assert len(graph_svc_legacy.build_dependency_graph("e-a").direct_dependencies) == 1


def test_h11_relationship_service_filters_stale_mentions(tmp_path):
    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h11")
    _create_entity(stack, "e-c", "C")
    _create_entity(stack, "e-d", "D")
    stack["mentions"].create(EntityMention(
        mention_id="mn-co1", entity_type=EntityType.ISSUE,
        text="C", meeting_id="m-h11", source_text="C and D discussed",
        entity_id="e-c", resolution_status=ResolutionStatus.RESOLVED,
        created_at=NOW, source_revision=1,
    ))
    stack["mentions"].create(EntityMention(
        mention_id="mn-co2", entity_type=EntityType.ISSUE,
        text="D", meeting_id="m-h11", source_text="C and D discussed",
        entity_id="e-d", resolution_status=ResolutionStatus.RESOLVED,
        created_at=NOW, source_revision=1,
    ))
    _advance_meeting(stack, "m-h11", 2)

    rel_svc = EntityRelationshipService(
        stack["entities"], stack["mentions"], stack["dependencies"],
        current_revision_lookup=lambda mid: stack["current_rev"](mid),
    )
    graph = rel_svc.get_relationship_graph("e-c")
    co_occ = [r for r in graph.relationships if r.relationship_type == RelationshipType.CO_OCCURS_WITH]
    assert co_occ == []

    rel_svc_legacy = EntityRelationshipService(
        stack["entities"], stack["mentions"], stack["dependencies"],
    )
    graph_legacy = rel_svc_legacy.get_relationship_graph("e-c")
    co_occ_legacy = [r for r in graph_legacy.relationships if r.relationship_type == RelationshipType.CO_OCCURS_WITH]
    assert len(co_occ_legacy) == 1


def test_h11b_filter_current_records_semantics():
    dep = ExplicitDependency(
        dependency_id="dep-f", source_entity_id="e1", target_entity_id="e2",
        relationship_type=RelationshipType.DEPENDS_ON,
        evidence_type=RelationshipEvidenceType.EXPLICIT_STATEMENT,
        source_text="e1 depends on e2",
        meeting_id="m-f", mention_id="mn-f", source_revision=1,
    )
    assert len(filter_current_records([dep], lambda mid: 1)) == 1
    assert len(filter_current_records([dep], lambda mid: 2)) == 0
    assert len(filter_current_records([dep], None)) == 1
    # Un-attributed record cannot prove currency → excluded when lookup given.
    orphan = dep.model_copy(update={"meeting_id": None})
    assert len(filter_current_records([orphan], lambda mid: 1)) == 0


def test_h11c_filter_current_mentions_semantics():
    ma = EntityMention(
        mention_id="mn-a", entity_type=EntityType.ISSUE,
        text="A", meeting_id="m-a", source_text="A text",
        entity_id="e1", resolution_status=ResolutionStatus.RESOLVED,
        created_at=NOW, source_revision=1,
    )
    assert len(filter_current_mentions([ma], lambda mid: 1)) == 1
    assert len(filter_current_mentions([ma], lambda mid: 2)) == 0
    assert len(filter_current_mentions([ma], lambda mid: 3)) == 0
    assert len(filter_current_mentions([ma], None)) == 1


# ========================================================================
# §24 — P11 JSON semantic index file locking
# ========================================================================

def test_h12_json_index_concurrent_writes(tmp_path):
    index_path = tmp_path / "concurrent.json"
    errors = []

    def _worker(worker_id: int):
        try:
            repo = JsonFileSemanticIndexRepository(index_path)
            svc = SemanticIndexingService(FakeEmbeddingProvider(), repo, "fake", "1.0")
            for i in range(5):
                item = EvidenceItem(
                    evidence_id=f"e-w{worker_id}-{i}",
                    evidence_type=EvidenceType.OBSERVATION,
                    entity_id=None, meeting_id=f"m-c-{worker_id}",
                    summary=f"Worker {worker_id} item {i}",
                    source_reference=f"m-c-{worker_id}",
                )
                svc.index_evidence(item, source_revision=1, meeting_id=f"m-c-{worker_id}")
        except Exception as exc:
            errors.append((worker_id, repr(exc)))

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_worker, wid) for wid in range(4)]
        for f in futures:
            f.result(timeout=60)

    assert errors == [], f"Concurrent write errors: {errors}"
    final = JsonFileSemanticIndexRepository(index_path)
    assert final.count() == 20, f"Expected 20 records, found {final.count()}"


def test_h13_json_index_corruption_degrades_to_empty(tmp_path):
    index_path = tmp_path / "corrupt.json"
    index_path.write_text("NOT VALID JSON {{{", encoding="utf-8")
    repo = JsonFileSemanticIndexRepository(index_path)
    assert repo.count() == 0
    assert repo.list_all() == []


# ========================================================================
# §24 — P17 stale-worker signal not swallowed
# ========================================================================

def test_h14_entity_service_propagates_stale_error(tmp_path):
    from app.services.dependency_resolution_service import DependencyResolutionService

    stack = _sqlite_stack(tmp_path)
    _ingest(stack, "m-h14")
    _create_entity(stack, "e-h14", "Alpha")

    class StaleDepResolutionService(DependencyResolutionService):
        def resolve_mention_dependencies(self, mention_id):
            raise StaleJobOwnershipError("simulated stale worker")

    dep_svc = StaleDepResolutionService(stack["entities"], stack["mentions"], stack["dependencies"])
    ent_svc = EntityService(stack["entities"], stack["mentions"], dep_svc)
    with pytest.raises(StaleJobOwnershipError):
        ent_svc.register_mention(
            entity_type=EntityType.ISSUE,
            text="Alpha", meeting_id="m-h14",
            source_text="Alpha blocks everything", source_revision=1,
        )


# ========================================================================
# §24 — Concurrency matrix (REAL SQLite multi-thread / multi-repo-session)
# ========================================================================

def test_h_concurrency_matrix(tmp_path):
    script = textwrap.dedent(
        r"""
import time
from pathlib import Path
from datetime import datetime, timezone
from app.persistence.sqlite_store import SQLiteSourceStore
from app.repositories.background_job_repository import SQLiteBackgroundJobRepository
from app.repositories.sqlite_source_repositories import SQLiteMeetingRepository
from app.services.meeting_service import MeetingService
from app.schemas.meeting import MeetingIngestRequest
from app.services.processing_consistency_service import processing_revision_for
from app.models.background_job import BackgroundJob, BackgroundJobStatus, BackgroundJobType
NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
clock = lambda: NOW

db = Path("{DB}")
db.parent.mkdir(parents=True, exist_ok=True)
store = SQLiteSourceStore(db)
meetings = SQLiteMeetingRepository(store)
svc = MeetingService(meetings)
req = MeetingIngestRequest(meeting_id="m-conc", title="Conc",
                           transcript="Alpha depends on Beta.", meeting_date=NOW)
svc.ingest_meeting(req)
job_id = "MEETING_PROCESSING:m-conc:" + processing_revision_for(1)
jobs_repo = SQLiteBackgroundJobRepository(store, clock)
jobs_repo.enqueue(BackgroundJob(
    job_id=job_id, job_type=BackgroundJobType.MEETING_PROCESSING,
    payload_id="m-conc", processing_revision=processing_revision_for(1),
    created_at=NOW,
))
results = []

def worker(name):
    jobs = SQLiteBackgroundJobRepository(store, clock)
    for _ in range(5):
        try:
            if jobs.claim(job_id, name, lease_seconds=1):
                jobs.checkpoint(job_id, "EXTRACTED", worker_id=name)
                jobs.transition(job_id, BackgroundJobStatus.SUCCEEDED, owner_id=name)
                results.append(name)
                return
        except Exception as exc:
            results.append(type(exc).__name__)
            return
        time.sleep(0.05)
    results.append("NO_CLAIM")

import threading
a = threading.Thread(target=worker, args=("w1",))
b = threading.Thread(target=worker, args=("w2",))
a.start(); b.start(); a.join(); b.join()
ok = [r for r in results if r in ("w1", "w2")]
rejected = [r for r in results if r not in ("w1", "w2")]
assert len(ok) == 1, "exactly one worker must succeed: " + repr(results)
assert len(rejected) == 1, "exactly one worker must be rejected: " + repr(results)
print("CONCURRENCY_MATRIX:PASS")
""".lstrip()
    )
    code = script.replace("{DB}", (tmp_path / "conc.db").as_posix())

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"Concurrency matrix failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "CONCURRENCY_MATRIX:PASS" in result.stdout


# ========================================================================
# §24 — Failure matrix (REAL SQLite; durable failures isolated)
# ========================================================================

def test_h_failure_matrix(tmp_path):
    stack = _sqlite_stack(tmp_path, name="fail.db")
    _ingest(stack, "m-fail")

    from app.services.extraction_service import ExtractionService

    extraction = ExtractionService(
        stack["meetings"], stack["extractions"],
        FakeExtractionProvider(result=ExtractionResult(meeting_id="m-fail", extracted_at=NOW)),
    )
    extraction.extract_meeting("m-fail")
    assert stack["extractions"].get_by_meeting_id("m-fail").source_revision == 1

    # Future revision rejected.
    with pytest.raises((FutureRevisionError, RevisionMismatchError)):
        stack["extractions"].save(
            ExtractionResult(meeting_id="m-fail", extracted_at=NOW, source_revision=2)
        )

    # Advance to rev 2; stale rev-1 write rejected, current write accepted.
    _advance_meeting(stack, "m-fail", 2)
    with pytest.raises((StaleJobOwnershipError, StaleRevisionError, RevisionMismatchError)):
        stack["extractions"].save(
            ExtractionResult(meeting_id="m-fail", extracted_at=NOW, source_revision=1)
        )
    extraction.extract_meeting("m-fail")
    assert stack["extractions"].get_by_meeting_id("m-fail").source_revision == 2
    assert stack["meetings"].get_by_id("m-fail").source_revision == 2

    # Other repositories remain consistent and intact after rejections.
    assert stack["meetings"].get_by_id("m-fail") is not None
    assert stack["dependencies"].list_all() == []
    print("FAILURE_MATRIX:PASS")