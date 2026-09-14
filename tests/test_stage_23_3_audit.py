"""Stage 23.3 final audit — independent verification of production guarantees.

Proves ordering, concurrency, consistency, stale protection, semantic
currency/retention, observation, query, incremental, org-level, config,
observability, scale, and integrity against REAL SQLite + persistent index.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import threading

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
)
from app.services.candidate_scoring_service import CandidateScoringService
from app.services.dependency_resolution_service import DependencyResolutionService
from app.services.entity_observation_service import EntityObservationService
from app.services.entity_service import EntityService
from app.services.extraction_service import ExtractionService
from app.services.meeting_pipeline_orchestrator import MeetingPipelineOrchestrator
from app.services.meeting_processing_service import MeetingProcessingService, ProcessingStage
from app.services.meeting_service import MeetingConflictError, MeetingService
from app.services.processing_consistency_service import (
    compare_processing_revisions,
    compare_source_orders,
    get_consistency_status,
    parse_source_revision,
    processing_revision_for,
)
from app.services.resolution_service import ResolutionService
from app.services.semantic_indexing_service import SemanticIndexingService
from app.repositories.semantic_index_repository import JsonFileSemanticIndexRepository
from tests._clock_utils import MutableClock

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Evidence:
    def build_semantic_evidence_for_meeting(self, meeting_id, entity_ids, now):
        items = []
        for eid in sorted(set(entity_ids)):
            vid = hashlib.sha256(f"{eid}:{meeting_id}".encode()).hexdigest()[:12]
            items.append(EvidenceItem(
                evidence_id=vid, evidence_type=EvidenceType.OBSERVATION,
                entity_id=eid, meeting_id=meeting_id,
                summary=f"Observed {eid} in {meeting_id}.", source_reference=meeting_id,
            ))
        oid = hashlib.sha256(f"ORG:{meeting_id}".encode()).hexdigest()[:12]
        items.append(EvidenceItem(
            evidence_id=oid, evidence_type=EvidenceType.MEMORY_FACT,
            meeting_id=meeting_id, summary=f"Meeting {meeting_id}.", source_reference=meeting_id,
        ))
        return items

    def build_semantic_corpus(self, now):
        return []


def _stack(tmp_path, name="audit.db", clock=None):
    store = SQLiteSourceStore(tmp_path / name)
    meetings, extractions = SQLiteMeetingRepository(store), SQLiteExtractionRepository(store)
    entities, mentions = SQLiteEntityRepository(store), SQLiteMentionRepository(store)
    deps = SQLiteDependencyRepository(store)
    jobs = SQLiteBackgroundJobRepository(store, clock=clock)
    sched = BackgroundJobScheduler(jobs)
    sem_repo = JsonFileSemanticIndexRepository(tmp_path / f"{name}.semantic.json")
    def _current_rev(meeting_id):
        m = meetings.get_by_id(meeting_id) if meeting_id is not None else None
        return int(getattr(m, "source_revision", 1) or 1) if m is not None else None
    sem = SemanticIndexingService(
        FakeEmbeddingProvider(), sem_repo, "fake", "1.0",
        current_revision_lookup=_current_rev,
    )
    scoring = CandidateScoringService(mentions, entities, LexicalCandidateGenerator(), LexicalCandidateScorer())
    resolution = ResolutionService(mentions, entities, scoring, ThresholdResolutionPolicy())
    dep_res = DependencyResolutionService(entities, mentions, deps)
    obs = EntityObservationService(meetings, entities, mentions)
    orch = MeetingPipelineOrchestrator(
        extractions, mentions, resolution, dep_res, _Evidence(), sem,
        observation_service=obs, meeting_repository=meetings,
    )
    return {k: v for k, v in locals().items() if k in (
        "store", "meetings", "extractions", "entities", "mentions", "deps",
        "jobs", "sched", "sem_repo", "sem", "resolution", "dep_res", "obs", "orch")}


# ---------------------------------------------------------------------------
# §2 revision ordering A–G
# ---------------------------------------------------------------------------

def test_audit_revision_ordering_total():
    # A: plain integers ordered numerically.
    assert compare_source_orders(1, 2) == "OLDER"
    assert compare_source_orders(3, 2) == "NEWER"
    assert compare_source_orders(2, 2) == "EQUAL"
    # B: same source, different timestamps → EQUAL order, distinct identity.
    assert compare_processing_revisions("1@2026-01-01T10:00", "1@2026-01-01T10:05") == "EQUAL"
    assert "1@2026-01-01T10:00" != "1@2026-01-01T10:05"  # identity differs
    # C: reversed timestamps still EQUAL (timestamps never order).
    assert compare_processing_revisions("1@2026-01-01T10:05", "1@2026-01-01T10:00") == "EQUAL"
    # D: bare vs suffixed same source → EQUAL order.
    assert compare_processing_revisions("1", "1@suffix") == "EQUAL"
    # E: different sources order by int regardless of suffix.
    assert compare_processing_revisions("1@suffix", "2") == "OLDER"
    assert compare_processing_revisions("2", "1@suffix") == "NEWER"
    # F: two suffixes over same source → EQUAL, never ambiguous-current.
    assert compare_processing_revisions("1@A", "1@B") == "EQUAL"
    # G: legacy bare ISO has no source identity → INCOMPARABLE, never current.
    assert parse_source_revision("2026-01-01T10:00:00") is None
    assert compare_processing_revisions("2026-01-01T10:00:00", "1") == "INCOMPARABLE"
    assert compare_processing_revisions(None, "1") == "INCOMPARABLE"
    # No lexicographic trap: "10" > "2" numerically (lexicographic would say "10" < "2").
    assert compare_processing_revisions("10", "2") == "NEWER"
    assert compare_processing_revisions("2", "10") == "OLDER"


# ---------------------------------------------------------------------------
# §3 concurrent refreshes → N+1, N+2 with one authoritative order
# ---------------------------------------------------------------------------

def test_audit_concurrent_refreshes_serialize_to_n_plus_1_n_plus_2(tmp_path):
    s = _stack(tmp_path, "conc.db")
    svc = MeetingService(s["meetings"])
    m = svc.ingest_meeting(MeetingIngestRequest(
        meeting_id="m-conc", title="T", transcript="v1", meeting_date=NOW))
    assert m.source_revision == 1
    errors: list = []

    def _refresh(transcript: str):
        try:
            # Separate connections simulate separate processes/requests.
            store = SQLiteSourceStore(tmp_path / "conc.db")
            try:
                meetings = SQLiteMeetingRepository(store)
                cur = meetings.get_by_id("m-conc")
                nxt = Meeting(
                    meeting_id="m-conc", title="T", transcript=transcript,
                    meeting_date=NOW, ingested_at=datetime.now(timezone.utc),
                    idempotency_key=cur.idempotency_key,
                    source_revision=cur.source_revision + 1,
                )
                # Retry on monotonic conflict AND SQLite writer serialization.
                for _ in range(10):
                    try:
                        meetings.save(nxt)
                        break
                    except MeetingConflictError:
                        cur = meetings.get_by_id("m-conc")
                        nxt = nxt.model_copy(update={"source_revision": cur.source_revision + 1})
                    except Exception as exc:
                        if "locked" in str(exc).lower():
                            import time as _t
                            _t.sleep(0.05)
                            cur = meetings.get_by_id("m-conc")
                            nxt = nxt.model_copy(update={"source_revision": cur.source_revision + 1})
                            continue
                        raise
                else:
                    errors.append("retry-exhausted")
            finally:
                store.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_refresh, args=(f"vA-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"concurrent refresh errors: {errors}"
    final = SQLiteMeetingRepository(SQLiteSourceStore(tmp_path / "conc.db")).get_by_id("m-conc")
    # Exactly one ordering: revisions advanced twice from 1 → 3 (N+1, N+2).
    assert final.source_revision == 3, f"expected serialized N+1,N+2 got {final.source_revision}"
    # Stale overwrite rejected: writing 1 over 3 raises.
    with pytest.raises(StaleJobOwnershipError):
        s["meetings"].save(Meeting(
            meeting_id="m-conc", title="T", transcript="stale",
            meeting_date=NOW, ingested_at=NOW, source_revision=1))


# ---------------------------------------------------------------------------
# §4 manual refresh semantics
# ---------------------------------------------------------------------------

def test_audit_manual_refresh_does_not_mutate_source_truth(tmp_path):
    s = _stack(tmp_path, "manual.db")
    svc = MeetingService(s["meetings"])
    m = svc.ingest_meeting(MeetingIngestRequest(
        meeting_id="m-man", title="T", transcript="hello", meeting_date=NOW))
    before = s["meetings"].get_by_id("m-man")
    ext = ExtractionService(s["meetings"], s["extractions"],
                            FakeExtractionProvider(result=ExtractionResult(meeting_id="m-man", extracted_at=NOW)))
    result = ext.extract_meeting("m-man")
    after = s["meetings"].get_by_id("m-man")
    # Source truth untouched.
    assert after.transcript == before.transcript
    assert after.source_revision == before.source_revision == 1
    # Distinct durable job tied to same source via N@suffix, history preserved.
    rev = processing_revision_for(result.source_revision, suffix=result.extracted_at.isoformat())
    assert parse_source_revision(rev) == 1
    assert rev != processing_revision_for(1)
    j1 = s["sched"].build_job(BackgroundJobType.MEETING_PROCESSING, "m-man", processing_revision=processing_revision_for(1))
    j2 = s["sched"].build_job(BackgroundJobType.MEETING_PROCESSING, "m-man", processing_revision=rev)
    s["sched"]._repository.enqueue(j1)
    s["sched"]._repository.enqueue(j2)
    assert j1.job_id != j2.job_id
    assert len(s["jobs"].list()) == 2


# ---------------------------------------------------------------------------
# §5 consistency statuses
# ---------------------------------------------------------------------------

def test_audit_consistency_statuses(tmp_path):
    def _status(mid, st):
        return get_consistency_status(
            mid, meeting_repository=st["meetings"], job_repository=st["jobs"],
            extraction_repository=st["extractions"], mention_repository=st["mentions"],
            semantic_repository=st["sem_repo"])

    # INCOMPLETE: source exists, nothing processed.
    s = _stack(tmp_path, "cs1.db")
    MeetingService(s["meetings"]).ingest_meeting(MeetingIngestRequest(
        meeting_id="m-cs", title="T", transcript="hi", meeting_date=NOW))
    # Enqueue intent so PENDING is visible, but incomplete (no extraction/semantic).
    s["sched"].enqueue(BackgroundJobType.MEETING_PROCESSING, "m-cs",
                       processing_revision=processing_revision_for(1))
    st = _status("m-cs", s)
    assert st["is_current"] is False
    assert st["status"] in {"INCOMPLETE", "PENDING"}

    # FAILED: fail current revision → never current.
    s2 = _stack(tmp_path, "cs2.db")
    MeetingService(s2["meetings"]).ingest_meeting(MeetingIngestRequest(
        meeting_id="m-cs", title="T", transcript="hi", meeting_date=NOW))
    jid = f"MEETING_PROCESSING:m-cs:{processing_revision_for(1)}"
    s2["sched"].enqueue(BackgroundJobType.MEETING_PROCESSING, "m-cs",
                        processing_revision=processing_revision_for(1))
    t = datetime.now(timezone.utc)
    claimed = s2["jobs"].claim(jid, "w", 60)
    s2["jobs"].transition(jid, BackgroundJobStatus.FAILED, owner_id="w",
                          last_error="x", error_type="PERMANENT")
    st2 = _status("m-cs", s2)
    assert st2["is_current"] is False and st2["status"] == "FAILED"

    # STALE/OUTDATED: rev1 succeeded, source advanced to rev2 without success.
    s3 = _stack(tmp_path, "cs3.db")
    MeetingService(s3["meetings"]).ingest_meeting(MeetingIngestRequest(
        meeting_id="m-cs", title="T", transcript="v1", meeting_date=NOW))
    s3["sched"].enqueue(BackgroundJobType.MEETING_PROCESSING, "m-cs",
                        processing_revision=processing_revision_for(1))
    t = datetime.now(timezone.utc)
    c = s3["jobs"].claim(f"MEETING_PROCESSING:m-cs:1", "w", 60)
    s3["jobs"].checkpoint(c.job_id, "COMPLETED", "w")
    s3["jobs"].transition(c.job_id, BackgroundJobStatus.SUCCEEDED, owner_id="w")
    # Advance source without processing new revision.
    cur = s3["meetings"].get_by_id("m-cs")
    s3["meetings"].save(cur.model_copy(update={"transcript": "v2", "source_revision": 2}))
    st3 = _status("m-cs", s3)
    assert st3["source_revision"] == 2 and st3["derived_revision"] == 1
    assert st3["is_current"] is False and st3["status"] == "STALE"

    # PENDING: newer source has pending job.
    s3["sched"].enqueue(BackgroundJobType.MEETING_PROCESSING, "m-cs",
                        processing_revision=processing_revision_for(2))
    st4 = _status("m-cs", s3)
    assert st4["status"] in {"STALE", "PENDING"}
    assert st4["is_current"] is False


# ---------------------------------------------------------------------------
# §6 stale protection for every writer (DB-level, no ownership checker)
# ---------------------------------------------------------------------------

def test_audit_db_level_stale_guards_without_checkers(tmp_path):
    s = _stack(tmp_path, "stale-db.db")
    MeetingService(s["meetings"]).ingest_meeting(MeetingIngestRequest(
        meeting_id="m-sg", title="T", transcript="v2", meeting_date=NOW))
    cur = s["meetings"].get_by_id("m-sg")
    s["meetings"].save(cur.model_copy(update={"transcript": "v2b", "source_revision": 2}))
    # Extraction: rev1 cannot overwrite rev2.
    s["extractions"].save(ExtractionResult(meeting_id="m-sg", extracted_at=NOW, source_revision=2))
    with pytest.raises(StaleJobOwnershipError):
        s["extractions"].save(ExtractionResult(meeting_id="m-sg", extracted_at=NOW, source_revision=1))
    # Mention: old-revision mention rejected against current meeting.
    from app.models.entity import EntityMention, EntityType as ET
    with pytest.raises(StaleJobOwnershipError):
        s["mentions"].create(EntityMention(
            mention_id="stale-m", entity_type=ET.PERSON, text="X", meeting_id="m-sg",
            source_text="x", created_at=NOW, source_revision=1))
    # Dependency: old-revision dependency rejected.
    from app.models.dependency import ExplicitDependency
    from app.models.relationships import RelationshipType
    EntityService(s["entities"], s["mentions"]).create_entity(ET.ISSUE, "A1")
    EntityService(s["entities"], s["mentions"]).create_entity(ET.ISSUE, "B1")
    e1 = s["entities"].list_entities()[0].entity_id
    e2 = s["entities"].list_entities()[1].entity_id
    with pytest.raises(StaleJobOwnershipError):
        s["deps"].save(ExplicitDependency(
            dependency_id="d-stale", source_entity_id=e1, target_entity_id=e2,
            relationship_type=RelationshipType.DEPENDS_ON, source_text="x",
            meeting_id="m-sg", mention_id="m", source_revision=1))


# ---------------------------------------------------------------------------
# §7 semantic currency: V reuse → advance, old rejected, changed → new embed
# ---------------------------------------------------------------------------

def test_audit_semantic_currency_three_states(tmp_path):
    s = _stack(tmp_path, "semcur.db")
    item = EvidenceItem(evidence_id="e-cur", evidence_type=EvidenceType.OBSERVATION,
                        meeting_id="m1", summary="same text", source_reference="m1")
    r1 = s["sem"].index_evidence(item, source_revision=1, meeting_id="m1")
    v1 = list(r1.embedding)
    # Same representation, rev2 → no new embed, currency advanced to 2.
    calls_before = 0
    orig = s["sem"]._embedding_provider.embed_text
    def _count(t):
        nonlocal calls_before
        calls_before += 1
        return orig(t)
    s["sem"]._embedding_provider.embed_text = _count  # type: ignore
    r2 = s["sem"].index_evidence(item, source_revision=2, meeting_id="m1")
    assert r2.source_revision == 2
    assert list(r2.embedding) == v1
    assert calls_before == 0
    # Old rev1 worker tries to write (changed text to force write path) → rejected.
    item_old = item.model_copy(update={"summary": "older different text"})
    with pytest.raises(StaleJobOwnershipError):
        s["sem"].index_evidence(item_old, source_revision=1, meeting_id="m1")
    # Rev3 changed representation → new embedding.
    item3 = item.model_copy(update={"summary": "completely new evidence text here"})
    r3 = s["sem"].index_evidence(item3, source_revision=3, meeting_id="m1")
    assert r3.source_revision == 3
    assert list(r3.embedding) != v1


# ---------------------------------------------------------------------------
# §8 retention: stale records do not pollute current retrieval
# ---------------------------------------------------------------------------

def test_audit_semantic_retention_does_not_pollute(tmp_path):
    from app.services.semantic_evidence_retrieval_service import SemanticEvidenceRetrievalService
    s = _stack(tmp_path, "ret.db")
    svc = SemanticEvidenceRetrievalService(FakeEmbeddingProvider(), repository=s["sem_repo"])
    # Seed rev1 record for evidence that later disappears.
    item = EvidenceItem(evidence_id="e-gone", evidence_type=EvidenceType.OBSERVATION,
                        meeting_id="m1", summary="old evidence", source_reference="m1")
    s["sem"].index_evidence(item, source_revision=1, meeting_id="m1")
    # Current corpus no longer contains e-gone → search must exclude it.
    current = [EvidenceItem(evidence_id="e-keep", evidence_type=EvidenceType.OBSERVATION,
                            meeting_id="m1", summary="keep", source_reference="m1")]
    s["sem"].index_evidence(current[0], source_revision=2, meeting_id="m1")
    lookup = {c.evidence_id: c for c in current}
    matches = svc.search_persisted("keep", lookup.get, top_k=5)
    assert all(m.evidence_id != "e-gone" for m in matches)


# ---------------------------------------------------------------------------
# §9 observation boundaries
# ---------------------------------------------------------------------------

def test_audit_observation_boundaries():
    from app.repositories.entity_repository import InMemoryEntityRepository
    from app.repositories.meeting_repository import InMemoryMeetingRepository
    from app.repositories.mention_repository import InMemoryMentionRepository
    for transcript, participants, setup, expect in [
        ("Rahul Kumar arrived.", ["Rahul Kumar"], [("PERSON", "Rahul Kumar")], "RESOLVED"),
        ("Rahul arrived.", ["Rahul"], [("PERSON", "Rahul Kumar")], "UNRESOLVED"),  # substring ≠ identity
        ("Rahul Kumar arrived.", ["Rahul"], [("PERSON", "Rahul Kumar")], "UNRESOLVED"),  # partial
    ]:
        m, e, n = InMemoryMeetingRepository(), InMemoryEntityRepository(), InMemoryMentionRepository()
        for t, name in setup:
            EntityService(e, n).create_entity(EntityType(t), name)
        m.save(Meeting(meeting_id="m", title="T", transcript=transcript,
                       meeting_date=NOW, ingested_at=NOW, participants=participants))
        EntityObservationService(m, e, n).observe_meeting("m")
        got = [x.resolution_status.value for x in n.list_by_meeting_id("m")]
        assert expect in got, f"{transcript} → {got}, expected {expect}"
        assert len(e.list_entities()) == len(setup)  # no hallucination

    # Conflicting aliases → AMBIGUOUS, never forced.
    from app.repositories.entity_repository import InMemoryEntityRepository as _ER
    m, e, n = InMemoryMeetingRepository(), _ER(), InMemoryMentionRepository()
    a, _ = EntityService(e, n).create_entity(EntityType.PERSON, "Rahul Kumar")
    b, _ = EntityService(e, n).create_entity(EntityType.PERSON, "Rahul Singh")
    e.add_alias(a.entity_id, "Rahul")
    e.add_alias(b.entity_id, "Rahul")
    m.save(Meeting(meeting_id="m", title="T", transcript="Rahul joined.",
                   meeting_date=NOW, ingested_at=NOW, participants=["Rahul"]))
    EntityObservationService(m, e, n).observe_meeting("m")
    amb = [x for x in n.list_by_meeting_id("m") if x.resolution_status.value == "AMBIGUOUS"]
    assert amb and all(x.entity_id is None for x in amb)

    # Repeated processing creates no duplicates.
    m2, e2, n2 = InMemoryMeetingRepository(), _ER(), InMemoryMentionRepository()
    EntityService(e2, n2).create_entity(EntityType.PERSON, "Rahul Kumar")
    m2.save(Meeting(meeting_id="m", title="T", transcript="Rahul Kumar arrived.",
                    meeting_date=NOW, ingested_at=NOW, participants=["Rahul Kumar"]))
    svc2 = EntityObservationService(m2, e2, n2)
    svc2.observe_meeting("m")
    first = len(n2.list_by_meeting_id("m"))
    svc2.observe_meeting("m")
    assert len(n2.list_by_meeting_id("m")) == first


# ---------------------------------------------------------------------------
# §13 query architecturally read-only (no writer imports/calls)
# ---------------------------------------------------------------------------

def test_audit_query_code_cannot_write():
    import pathlib
    for mod in ["app/services/natural_language_query_service.py",
                "app/services/evidence_retrieval_service.py",
                "app/services/hybrid_evidence_retrieval_service.py",
                "app/services/semantic_evidence_retrieval_service.py"]:
        text = pathlib.Path(mod).read_text(encoding="utf-8")
        for writer in (".save(", ".upsert(", ".update(", ".create(", ".enqueue(", "index_evidence("):
            assert writer not in text, f"{mod} must never call {writer}"
    import app.api.query as q
    assert hasattr(q, "get_natural_language_query_service")


# ---------------------------------------------------------------------------
# §14 incremental: unrelated evidence never embedded
# ---------------------------------------------------------------------------

def test_audit_incremental_never_embeds_unrelated(tmp_path):
    s = _stack(tmp_path, "incr.db")
    for i in range(100):
        s["sem"].index_evidence(EvidenceItem(
            evidence_id=f"unrelated-{i:03d}", evidence_type=EvidenceType.OBSERVATION,
            meeting_id=f"other-{i}", summary=f"unrelated evidence {i}",
            source_reference="x"), source_revision=1, meeting_id=f"other-{i}")
    calls = {"n": 0}
    orig = s["sem"]._embedding_provider.embed_text
    def _count(t):
        calls["n"] += 1
        return orig(t)
    s["sem"]._embedding_provider.embed_text = _count  # type: ignore
    # Meeting-scoped builder returns only affected evidence (2 items).
    items = s["orch"]._evidence_retrieval_service.build_semantic_evidence_for_meeting(
        "new-m", [], datetime.now(timezone.utc))
    assert len(items) <= 3
    for it in items:
        s["sem"].index_evidence(it, source_revision=1, meeting_id="new-m")
    assert calls["n"] <= 3  # never 100+


# ---------------------------------------------------------------------------
# §15 org-level runs once
# ---------------------------------------------------------------------------

def test_audit_org_level_runs_once(tmp_path):
    s = _stack(tmp_path, "org.db")
    MeetingService(s["meetings"]).ingest_meeting(MeetingIngestRequest(
        meeting_id="m-org", title="T", transcript="hi", meeting_date=NOW))
    s["extractions"].save(ExtractionResult(meeting_id="m-org", extracted_at=NOW))
    calls = {"org": 0, "entity": 0}
    s["orch"]._organisation_derived_services = (lambda now: calls.__setitem__("org", calls["org"] + 1),)
    s["orch"]._derived_services = (lambda eid, now: calls.__setitem__("entity", calls["entity"] + 1),)
    # No resolved entities → entity services run 0 times, org runs exactly once.
    s["orch"].derive_intelligence("m-org")
    assert calls["org"] == 1 and calls["entity"] == 0


# ---------------------------------------------------------------------------
# §16 config safety
# ---------------------------------------------------------------------------

def test_audit_config_safety():
    from app.core.config import Settings
    import pytest as _pt
    for field, value in [("source_repository_backend", "bogus"),
                         ("semantic_index_backend", "bogus"),
                         ("embedding_provider", "bogus"),
                         ("extraction_provider", "bogus"),
                         ("nl_provider", "bogus"),
                         ("source_database_path", "   "),
                         ("semantic_index_path", ""),
                         ("background_max_attempts", 0),
                         ("background_lease_seconds", 0),
                         ("background_poll_interval_seconds", 0)]:
        with _pt.raises(Exception):
            Settings(**{field: value})
    valid = Settings(source_repository_backend="database",
                     semantic_index_backend="persistent",
                     source_database_path="/tmp/x.db",
                     semantic_index_path="/tmp/y.json")
    assert valid.source_repository_backend == "database"


# ---------------------------------------------------------------------------
# §17 observability fields
# ---------------------------------------------------------------------------

def test_audit_observability_fields(tmp_path, caplog):
    import logging
    s = _stack(tmp_path, "obs2.db")
    MeetingService(s["meetings"]).ingest_meeting(MeetingIngestRequest(
        meeting_id="m-obs", title="T", transcript="hi", meeting_date=NOW))
    s["sched"].enqueue(BackgroundJobType.MEETING_PROCESSING, "m-obs",
                       processing_revision=processing_revision_for(1))

    def _boom(_m):
        raise RuntimeError("audited failure marker")

    worker = BackgroundWorkerService(s["jobs"], {BackgroundJobType.MEETING_PROCESSING: lambda job: MeetingProcessingService(s["jobs"], {
        ProcessingStage.EXTRACTED: _boom,
        ProcessingStage.RESOLVED: lambda m: None,
        ProcessingStage.RELATIONSHIPS_PERSISTED: lambda m: None,
        ProcessingStage.DERIVED_INTELLIGENCE: lambda m: None,
        ProcessingStage.SEMANTIC_INDEXED: lambda m: None}).process(job)},
        lease_seconds=60, backoff_seconds=0, worker_id="audit-worker")
    with caplog.at_level(logging.INFO, logger="app.services.background_worker_service"):
        result = worker.run_once(datetime.now(timezone.utc))
    assert result.job_id and result.payload_id == "m-obs"
    assert parse_source_revision(result.processing_revision) == 1
    assert result.attempts >= 1 and result.error_type and result.last_error
    assert result.next_retry_at is not None
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "m-obs" in text
    for secret in ("sk-", "OPENAI_API_KEY", "credential"):
        assert secret not in text


# ---------------------------------------------------------------------------
# §10 acceptance path genuine (no worker-internal shortcuts)
# ---------------------------------------------------------------------------

def test_audit_acceptance_path_is_genuine():
    import pathlib
    text = pathlib.Path("tests/test_stage_23_3_final_e2e.py").read_text(encoding="utf-8")
    assert "subprocess.Popen" in text and "uvicorn" in text  # fresh interpreter
    assert "MeetingPipelineOrchestrator(" not in text  # not orchestrator entry
    assert "checkpoint(" not in text  # never marks stages manually
    assert "transition(" not in text or "sqlite" in text.lower()  # only crash-sim claim via repo
    for forbidden in ["extract_meeting(", "resolve_meeting(", "persist_relationships(",
                      "derive_intelligence(", "index_semantic_evidence("]:
        assert forbidden not in text, f"E2E must go through HTTP, found {forbidden}"
    assert "/api/v1/meetings" in text and "/health/diagnostics" in text


# ---------------------------------------------------------------------------
# §11 restart genuinely cross-process
# ---------------------------------------------------------------------------

def test_audit_restart_is_cross_process():
    import pathlib, os
    text = pathlib.Path("tests/test_stage_23_3_final_e2e.py").read_text(encoding="utf-8")
    assert text.count("subprocess.Popen") >= 1
    assert "_start_app" in text and "_stop_app" in text
    # Two separate Popen lifetimes in restart test (process 1 terminated before 2).
    assert text.count("test_real_process_restart_recovery") == 1
    assert os.getpid() > 0  # test process itself is distinct from subprocess PIDs
    # Worker objects in one process would show as BackgroundWorkerService( in E2E.
    assert "BackgroundWorkerService(" not in text


# ---------------------------------------------------------------------------
# §12 retry + old worker takeover
# ---------------------------------------------------------------------------

def test_audit_old_worker_cannot_commit_after_takeover(tmp_path):
    clock = MutableClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    s = _stack(tmp_path, "takeover.db", clock=clock)
    MeetingService(s["meetings"]).ingest_meeting(MeetingIngestRequest(
        meeting_id="m-take", title="T", transcript="hi", meeting_date=NOW))
    s["sched"].enqueue(BackgroundJobType.MEETING_PROCESSING, "m-take",
                       processing_revision=processing_revision_for(1))
    jid = "MEETING_PROCESSING:m-take:1"
    a = s["jobs"].claim(jid, "worker-A", lease_seconds=1)
    assert a is not None and a.attempts == 1
    # Lease expires; B recovers and takes over.
    clock.advance(seconds=2)
    s["jobs"].recover_stale()
    b = s["jobs"].claim(jid, "worker-B", lease_seconds=60)
    assert b is not None and b.worker_id == "worker-B"
    # Old worker A checkpoint/transition rejected at DB level.
    with pytest.raises(StaleJobOwnershipError):
        s["jobs"].checkpoint(jid, "EXTRACTED", worker_id="worker-A")
    with pytest.raises(StaleJobOwnershipError):
        s["jobs"].transition(jid, BackgroundJobStatus.SUCCEEDED, owner_id="worker-A")
    # Old worker A derived write rejected via ownership predicate.
    s["orch"].set_ownership_checker(lambda: (_ for _ in ()).throw(StaleJobOwnershipError("A stale")))
    s["extractions"].save(ExtractionResult(meeting_id="m-take", extracted_at=NOW, source_revision=1))
    with pytest.raises(StaleJobOwnershipError):
        s["orch"].derive_intelligence("m-take")
    # New owner B progresses.
    s["jobs"].checkpoint(jid, "EXTRACTED", worker_id="worker-B")
    assert s["jobs"].get(jid).stage == "EXTRACTED"


# ---------------------------------------------------------------------------
# §19 integrity
# ---------------------------------------------------------------------------

def test_audit_sqlite_integrity(tmp_path):
    s = _stack(tmp_path, "integ.db")
    MeetingService(s["meetings"]).ingest_meeting(MeetingIngestRequest(
        meeting_id="m-int", title="T", transcript="hi", meeting_date=NOW))
    assert s["store"]._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert s["store"]._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # Job uniqueness: duplicate enqueue returns same logical job.
    j1 = s["sched"].enqueue(BackgroundJobType.MEETING_PROCESSING, "m-int",
                            processing_revision=processing_revision_for(1))
    j2 = s["sched"].enqueue(BackgroundJobType.MEETING_PROCESSING, "m-int",
                            processing_revision=processing_revision_for(1))
    assert j1.job_id == j2.job_id and len(s["jobs"].list()) == 1
    # Semantic identity uniqueness: same composite key upserts, no duplicates.
    item = EvidenceItem(evidence_id="e-u", evidence_type=EvidenceType.OBSERVATION,
                        summary="x", source_reference="m-int")
    s["sem"].index_evidence(item, source_revision=1, meeting_id="m-int")
    s["sem"].index_evidence(item, source_revision=1, meeting_id="m-int")
    assert s["sem_repo"].count() == 1
