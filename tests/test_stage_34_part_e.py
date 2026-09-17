"""Stage 34 Part E: proactive intelligence regression tests.

Covers: migration v6 / result_summary plumbing, record_scan_result
ownership, org discovery, scanner semantics (baseline, new-signal diff,
idempotent repeat, watermark, tenant scope), scheduler duplicate
prevention, worker restart/retry, config validation, scan-status endpoint.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.background_job import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.models.meeting import Meeting
from app.persistence.sqlite_store import SCHEMA_VERSION, SQLiteSourceStore
from app.repositories.auth_repositories import InMemoryAuthRepository
from app.repositories.background_job_repository import (
    InMemoryBackgroundJobRepository,
    SQLiteBackgroundJobRepository,
    StaleJobOwnershipError,
)
from app.services.background_worker_service import BackgroundWorkerService
from app.services.proactive_intelligence_service import (
    MAX_SIGNAL_IDS,
    ProactiveIntelligenceScanner,
    build_scan_job,
    plan_proactive_scans,
    scan_job_id,
    time_bucket,
)

_NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _scan_job(org_id: str, job_id: str = "scan-1") -> BackgroundJob:
    return BackgroundJob(
        job_id=job_id,
        job_type=BackgroundJobType.ORGANISATION_INTELLIGENCE_SCAN,
        status=BackgroundJobStatus.PENDING,
        created_at=_NOW,
        payload_id=org_id,
        organisation_id=org_id,
    )


def _meeting(meeting_id: str, revision: int = 1) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=f"Meeting {meeting_id}",
        meeting_date=_NOW,
        ingested_at=_NOW,
        transcript="dummy transcript",
        source_revision=revision,
    )


class _StubMeetings:
    def __init__(self, meetings):
        self._meetings = meetings

    def list_meetings(self):
        return list(self._meetings)


class _StubEntities:
    def __init__(self, ids):
        self._ids = ids

    def list_entities(self):
        return [SimpleNamespace(entity_id=i) for i in self._ids]


def _stub_graph(meetings=(), entities=(), changes=(), attention=(), insights=(), actions=(), portfolio=None):
    return {
        "repos": SimpleNamespace(
            meetings=_StubMeetings(meetings),
            entities=_StubEntities(entities),
        ),
        "intelligence": {
            "changes": SimpleNamespace(
                get_changes=lambda current_time=None, **k: list(changes)
            ),
            "attention": SimpleNamespace(
                get_attention=lambda current_time=None, **k: list(attention)
            ),
            "insights": SimpleNamespace(
                get_entity_insights=lambda eid, now: list(insights)
            ),
            "actions": SimpleNamespace(
                get_entity_actions=lambda eid, now: list(actions)
            ),
            "portfolio": SimpleNamespace(
                get_portfolio=lambda now: SimpleNamespace(
                    model_dump=lambda mode="json": portfolio or {"empty": True}
                )
            ),
        },
    }


def _sig(prefix, sid):
    return SimpleNamespace(**{f"{prefix}_id": sid})


def _enqueued(jobs, org_id: str, job_id: str) -> BackgroundJob:
    """Enqueue a scan job row (PENDING).  No claim needed: run_scan records
    with worker_id=None for unclaimed jobs, which skips the ownership check
    exactly as the repository contract allows."""
    job = _scan_job(org_id, job_id)
    jobs.enqueue(job)
    return job


# ---------------------------------------------------------------------------
# Migration + plumbing
# ---------------------------------------------------------------------------


class TestScanResultPlumbing:
    def test_schema_version_is_6(self):
        assert SCHEMA_VERSION == 6

    def test_fresh_store_has_result_summary_column(self, tmp_path):
        store = SQLiteSourceStore(tmp_path / "source.db")
        try:
            assert store.migration_version() == 6
            cols = {
                row[1]
                for row in store._connection.execute(
                    "PRAGMA table_info(background_jobs)"
                ).fetchall()
            }
            assert "result_summary" in cols
        finally:
            store.close()

    def test_migration_idempotent(self, tmp_path):
        path = tmp_path / "source.db"
        first = SQLiteSourceStore(path)
        first.close()
        second = SQLiteSourceStore(path)
        try:
            assert second.migration_version() == 6
        finally:
            second.close()

    def test_sqlite_summary_round_trip(self, tmp_path):
        store = SQLiteSourceStore(tmp_path / "source.db")
        try:
            repo = SQLiteBackgroundJobRepository(store)
            repo.enqueue(_scan_job("org-a", "scan-a"))
            claimed = repo.claim("scan-a", "w1", 60)
            repo.record_scan_result(
                "scan-a", '{"watermark": 3}', processing_revision="3",
                worker_id="w1",
            )
            stored = repo.get("scan-a")
            assert stored.result_summary == '{"watermark": 3}'
            assert stored.processing_revision == "3"
            repo.transition("scan-a", BackgroundJobStatus.SUCCEEDED, owner_id="w1")
            assert repo.get("scan-a").result_summary == '{"watermark": 3}'
        finally:
            store.close()

    def test_record_scan_result_rejects_stale_owner(self):
        repo = InMemoryBackgroundJobRepository()
        repo.enqueue(_scan_job("org-a", "scan-a"))
        repo.claim("scan-a", "w1", 60)
        with pytest.raises(StaleJobOwnershipError):
            repo.record_scan_result("scan-a", "{}", worker_id="intruder")

    def test_inmemory_summary_round_trip(self):
        repo = InMemoryBackgroundJobRepository()
        repo.enqueue(_scan_job("org-a", "scan-a"))
        repo.claim("scan-a", "w1", 60)
        repo.record_scan_result("scan-a", '{"k": 1}', worker_id="w1")
        assert repo.get("scan-a").result_summary == '{"k": 1}'


class TestOrgDiscovery:
    def test_lists_active_org_ids_sorted(self):
        from app.auth.models import Organisation, OrganisationStatus

        repo = InMemoryAuthRepository()
        for oid, status in (("org-b", "ACTIVE"), ("org-a", "ACTIVE"), ("org-z", "SUSPENDED")):
            repo.create_organisation(
                Organisation(
                    organisation_id=oid, name=oid, slug=oid,
                    status=OrganisationStatus(status),
                    created_at=_NOW, updated_at=_NOW,
                )
            )
        assert repo.list_organisation_ids() == ["org-a", "org-b"]
        assert repo.list_organisation_ids(active_only=False) == ["org-a", "org-b", "org-z"]

    def test_sqlite_lists_org_ids(self, tmp_path):
        from app.auth.models import Organisation, OrganisationStatus
        from app.repositories.auth_repositories import SQLiteAuthRepository

        store = SQLiteSourceStore(tmp_path / "source.db")
        try:
            repo = SQLiteAuthRepository(store)
            repo.create_organisation(
                Organisation(
                    organisation_id="org-a", name="A", slug="a",
                    status=OrganisationStatus.ACTIVE,
                    created_at=_NOW, updated_at=_NOW,
                )
            )
            assert repo.list_organisation_ids() == ["org-a"]
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Scanner semantics
# ---------------------------------------------------------------------------


class TestScanner:
    def _scanner(self, graph, jobs=None):
        jobs = jobs if jobs is not None else InMemoryBackgroundJobRepository()
        return ProactiveIntelligenceScanner(
            job_repository=jobs, build_graph=lambda org: graph
        ), jobs

    def test_first_scan_is_baseline_with_empty_new_list(self):
        graph = _stub_graph(
            meetings=[_meeting("m1", 2)],
            changes=[_sig("change", "c1")],
            attention=[_sig("attention", "a1")],
        )
        jobs = InMemoryBackgroundJobRepository()
        scanner, _ = self._scanner(graph, jobs)
        result = scanner.run_scan(_enqueued(jobs, "org-a", "scan-1"))
        assert result.watermark == 2
        assert "chg:c1" in result.signal_ids
        assert "att:a1" in result.signal_ids
        assert result.new_signal_ids == ()
        assert result.previous_completed_at is None

    def test_repeat_scan_over_unchanged_state_reports_no_new_signals(self):
        graph = _stub_graph(changes=[_sig("change", "c1")])
        jobs = InMemoryBackgroundJobRepository()
        scanner, _ = self._scanner(graph, jobs)
        scanner.run_scan(_enqueued(jobs, "org-a", "scan-1"))
        jobs.claim("scan-1", "w", 60)
        jobs.transition("scan-1", BackgroundJobStatus.SUCCEEDED)
        second = scanner.run_scan(_enqueued(jobs, "org-a", "scan-2"))
        first_data = json.loads(jobs.get("scan-1").result_summary)
        assert second.signal_ids == tuple(first_data["signal_ids"])
        assert second.new_signal_ids == ()
        assert second.previous_completed_at is not None

    def test_new_signal_detected_only_for_added_ids(self):
        jobs = InMemoryBackgroundJobRepository()
        scanner_v1, _ = self._scanner(
            _stub_graph(changes=[_sig("change", "c1")]), jobs
        )
        scanner_v1.run_scan(_enqueued(jobs, "org-a", "scan-1"))
        jobs.claim("scan-1", "w", 60)
        jobs.transition("scan-1", BackgroundJobStatus.SUCCEEDED)
        scanner_v2, _ = self._scanner(
            _stub_graph(
                changes=[_sig("change", "c1"), _sig("change", "c2")],
                attention=[_sig("attention", "a9")],
            ),
            jobs,
        )
        result = scanner_v2.run_scan(_enqueued(jobs, "org-a", "scan-2"))
        assert sorted(result.new_signal_ids) == ["att:a9", "chg:c2"]

    def test_summary_persisted_on_job_row(self):
        graph = _stub_graph(meetings=[_meeting("m1", 4)])
        jobs = InMemoryBackgroundJobRepository()
        scanner, _ = self._scanner(graph, jobs)
        job = _scan_job("org-a", "scan-1")
        jobs.enqueue(job)
        jobs.claim("scan-1", "w", 60)
        scanner.run_scan(jobs.get("scan-1"))
        stored = jobs.get("scan-1")
        data = json.loads(stored.result_summary)
        assert data["watermark"] == 4
        assert stored.processing_revision == "4"
        assert data["schema_version"] == 1

    def test_uses_durable_job_scope_not_caller_state(self):
        seen = []

        def build_graph(org_id):
            seen.append(org_id)
            return _stub_graph()

        jobs = InMemoryBackgroundJobRepository()
        scanner = ProactiveIntelligenceScanner(
            job_repository=jobs,
            build_graph=build_graph,
        )
        scanner.run_scan(_enqueued(jobs, "org-B", "scan-1"))
        assert seen == ["org-B"]

    def test_empty_org_succeeds_with_no_signals(self):
        jobs = InMemoryBackgroundJobRepository()
        scanner, _ = self._scanner(_stub_graph(), jobs)
        result = scanner.run_scan(_enqueued(jobs, "org-empty", "scan-1"))
        assert result.watermark == 0
        assert result.signal_ids == ()
        assert result.new_signal_ids == ()

    def test_signal_set_stable_across_wall_clock_time(self):
        # Guards E9: identical source state scanned at different times must
        # yield identical signals (no wall-clock-derived IDs that would
        # manufacture "new" alerts on every scan).
        from datetime import timedelta

        graph = _stub_graph(
            meetings=[_meeting("m1", 2)],
            changes=[_sig("change", "c1")],
            attention=[_sig("attention", "a1")],
        )
        jobs = InMemoryBackgroundJobRepository()
        scanner, _ = self._scanner(graph, jobs)
        first = scanner.run_scan(_enqueued(jobs, "org-a", "scan-1"))
        later = _NOW + timedelta(days=1)
        second = scanner.run_scan(_enqueued(jobs, "org-a", "scan-2"), now=later)
        assert second.signal_ids == first.signal_ids


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class TestScheduler:
    def _auth(self, orgs):
        from app.auth.models import Organisation, OrganisationStatus

        repo = InMemoryAuthRepository()
        for oid in orgs:
            repo.create_organisation(
                Organisation(
                    organisation_id=oid, name=oid, slug=oid,
                    status=OrganisationStatus.ACTIVE,
                    created_at=_NOW, updated_at=_NOW,
                )
            )
        return repo

    def test_plans_scans_for_orgs_without_outstanding(self):
        jobs = InMemoryBackgroundJobRepository()
        assert plan_proactive_scans(
            job_repository=jobs, auth_repository=self._auth(["a", "b"])
        ) == ["a", "b"]

    def test_skips_org_with_outstanding_scan(self):
        jobs = InMemoryBackgroundJobRepository()
        jobs.enqueue(_scan_job("a", "scan-a"))
        assert plan_proactive_scans(
            job_repository=jobs, auth_repository=self._auth(["a", "b"])
        ) == ["b"]

    def test_succeeded_history_does_not_block_new_scan(self):
        jobs = InMemoryBackgroundJobRepository()
        jobs.enqueue(_scan_job("a", "scan-a"))
        jobs.claim("scan-a", "w", 60)
        jobs.transition("scan-a", BackgroundJobStatus.SUCCEEDED, owner_id="w")
        assert plan_proactive_scans(
            job_repository=jobs, auth_repository=self._auth(["a"])
        ) == ["a"]

    def test_bucketed_job_ids_deterministic_per_bucket(self):
        assert scan_job_id("org", 7) == scan_job_id("org", 7)
        assert scan_job_id("org", 7) != scan_job_id("org", 8)
        assert scan_job_id("org", 7) != scan_job_id("other", 7)

    def test_enqueue_dedups_same_bucket(self):
        jobs = InMemoryBackgroundJobRepository()
        jobs.enqueue(build_scan_job("org", 7, now=_NOW))
        jobs.enqueue(build_scan_job("org", 7, now=_NOW))
        assert len(jobs.list()) == 1

    def test_time_bucket_advances_with_interval(self):
        assert time_bucket(_NOW, 3600.0) == time_bucket(_NOW, 3600.0)
        later = datetime(2026, 9, 1, 14, 0, 0, tzinfo=timezone.utc)
        assert time_bucket(later, 3600.0) != time_bucket(_NOW, 3600.0)

    def test_build_scan_job_carries_durable_scope(self):
        job = build_scan_job("org-x", 3, now=_NOW, max_attempts=3)
        assert job.job_type == BackgroundJobType.ORGANISATION_INTELLIGENCE_SCAN
        assert job.organisation_id == "org-x"
        assert job.payload_id == "org-x"
        assert job.status == BackgroundJobStatus.PENDING


# ---------------------------------------------------------------------------
# Worker integration: retry + restart idempotency
# ---------------------------------------------------------------------------


class TestWorkerIntegration:
    def _worker(self, jobs, graph):
        scanner = ProactiveIntelligenceScanner(
            job_repository=jobs, build_graph=lambda org: graph
        )

        def handler(job):
            scanner.run_scan(job)

        return BackgroundWorkerService(
            jobs, {BackgroundJobType.ORGANISATION_INTELLIGENCE_SCAN: handler}
        )

    def test_run_once_completes_scan_with_summary(self):
        jobs = InMemoryBackgroundJobRepository()
        jobs.enqueue(_scan_job("org-a", "scan-1"))
        worker = self._worker(jobs, _stub_graph(changes=[_sig("change", "c1")]))
        done = worker.run_once(now=_NOW)
        assert done.status == BackgroundJobStatus.SUCCEEDED
        data = json.loads(done.result_summary)
        assert data["signal_count"] >= 1
        assert data["new_signal_ids"] == []

    def test_transient_failure_retries_then_fails_without_fabrication(self):
        jobs = InMemoryBackgroundJobRepository()
        job = _scan_job("org-a", "scan-1")
        job.max_attempts = 2
        jobs.enqueue(job)

        calls = []

        def flaky(claimed):
            calls.append(1)
            raise ValueError("transient boom")

        worker = BackgroundWorkerService(
            jobs,
            {BackgroundJobType.ORGANISATION_INTELLIGENCE_SCAN: flaky},
            backoff_seconds=0,
            clock=lambda: _NOW,
        )
        first = worker.run_once(now=_NOW)
        assert first.status == BackgroundJobStatus.RETRY_WAITING
        second = worker.run_once(now=_NOW)
        assert second.status == BackgroundJobStatus.FAILED
        assert second.error_type == "TRANSIENT"
        assert jobs.get("scan-1").result_summary is None
        assert len(calls) == 2

    def test_crashed_scan_recovers_and_completes_after_restart(self):
        box = [_NOW]
        jobs = InMemoryBackgroundJobRepository(clock=lambda: box[0])
        graph = _stub_graph(changes=[_sig("change", "c1")])
        jobs.enqueue(_scan_job("org-a", "scan-1"))
        # Crash simulation: claimed at T0, worker died before completion.
        jobs.claim("scan-1", "dead-worker", 60)
        assert jobs.get("scan-1").status == BackgroundJobStatus.RUNNING
        # "Restart" much later with a fresh worker over the same repo.
        box[0] = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        worker = self._worker(jobs, graph)
        done = worker.run_once(now=box[0])
        assert done.status == BackgroundJobStatus.SUCCEEDED
        data = json.loads(done.result_summary)
        assert data["signal_count"] >= 1
        assert done.processing_revision == "0"

    def test_repeat_execution_rewrites_identical_summary(self):
        jobs = InMemoryBackgroundJobRepository()
        graph = _stub_graph(changes=[_sig("change", "c1")])
        jobs.enqueue(_scan_job("org-a", "scan-1"))
        worker = self._worker(jobs, graph)
        done = worker.run_once(now=_NOW)
        first_summary = done.result_summary
        # Simulate a second execution of the same logical scan (retry after
        # a crash before completion was recorded): same inputs → same summary.
        scanner = ProactiveIntelligenceScanner(
            job_repository=jobs, build_graph=lambda org: graph
        )
        repeat = scanner.run_scan(_scan_job("org-a", "scan-1"))
        assert json.loads(first_summary)["signal_ids"] == list(repeat.signal_ids)


class TestScanConfig:
    def test_defaults_disabled_with_positive_interval(self):
        from app.core.config import Settings

        settings = Settings()
        assert settings.proactive_intelligence_enabled is False
        assert settings.proactive_intelligence_interval_seconds > 0

    def test_zero_or_negative_interval_rejected(self):
        from app.core.config import Settings

        with pytest.raises(ValueError):
            Settings(proactive_intelligence_interval_seconds=0)
        with pytest.raises(ValueError):
            Settings(proactive_intelligence_interval_seconds=-5)


# ---------------------------------------------------------------------------
# Scan-status endpoint (bootstrap-open test app: default org scope)
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client():
    return TestClient(app)


def _seed_succeeded_scan(org_id, summary, job_id="e2e-scan-1"):
    from app.api.jobs import get_job_repository

    repo = get_job_repository()
    repo.enqueue(_scan_job(org_id, job_id))
    claimed = repo.claim(job_id, "w", 60)
    repo.record_scan_result(job_id, summary, worker_id="w")
    repo.transition(job_id, BackgroundJobStatus.SUCCEEDED, owner_id="w")
    return repo


class TestScanStatusEndpoint:
    def test_no_history_reports_not_scanned(self, api_client):
        response = api_client.get("/api/v1/intelligence/scan-status")
        assert response.status_code == 200
        assert response.json()["scanned"] is False

    def test_latest_scan_reported(self, api_client):
        summary = json.dumps({
            "schema_version": 1, "watermark": 2, "signal_count": 3,
            "signal_ids": ["chg:c1"], "new_signal_ids": ["chg:c1"],
            "previous_completed_at": None, "completed_at": _NOW.isoformat(),
            "truncated": False,
        })
        repo = _seed_succeeded_scan("default", summary, "e2e-scan-2")
        try:
            response = api_client.get("/api/v1/intelligence/scan-status")
            assert response.status_code == 200
            body = response.json()
            assert body["scanned"] is True
            assert body["watermark"] == 2
            assert body["signal_count"] == 3
            assert body["new_signal_ids"] == ["chg:c1"]
        finally:
            getattr(repo, "_jobs", {}).pop("e2e-scan-2", None)

    def test_corrupt_summary_reports_not_scanned(self, api_client):
        repo = _seed_succeeded_scan("default", "not-json{{{", "e2e-scan-3")
        try:
            response = api_client.get("/api/v1/intelligence/scan-status")
            assert response.status_code == 200
            assert response.json()["scanned"] is False
        finally:
            getattr(repo, "_jobs", {}).pop("e2e-scan-3", None)
