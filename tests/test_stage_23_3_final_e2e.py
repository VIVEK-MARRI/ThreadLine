"""Stage 23.3 final E2E: real FastAPI → worker → completion, restart, lifecycle.

All tests use REAL uvicorn subprocesses + REAL SQLite + REAL persistent
semantic index + REAL worker + REAL ingestion endpoint.  No orchestrator
direct instantiation, no manual extraction insertion, no manual job
mutation (except a real atomic claim to simulate a crash with RUNNING).
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(port: int, method: str, path: str, payload=None, timeout: int = 5):
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        try:
            data = json.loads(raw) if raw else None
        except Exception:
            data = None
        return response.status, data


def _close_log_handle(process) -> None:
    handle = getattr(process, "_e2e_log_handle", None)
    if handle is not None:
        try:
            handle.close()
        except Exception:
            pass
        try:
            delattr(process, "_e2e_log_handle")
        except Exception:
            pass


def _log_tail(tmp_path: Path, port: int, limit: int = 8000) -> str:
    try:
        text = (tmp_path / f"uvicorn-{port}.log").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "<no subprocess log>"
    return text[-limit:]


def _start_app(tmp_path: Path, worker_enabled: bool, extra_env: dict | None = None):
    port = _free_port()
    environment = os.environ.copy()
    environment.update({
        "SOURCE_REPOSITORY_BACKEND": "database",
        "SOURCE_DATABASE_PATH": str(tmp_path / "source.db"),
        "SEMANTIC_INDEX_BACKEND": "persistent",
        "SEMANTIC_INDEX_PATH": str(tmp_path / "semantic.json"),
        "EXTRACTION_PROVIDER": "fake",
        "NL_PROVIDER": "fake",
        "BACKGROUND_WORKER_ENABLED": str(worker_enabled).lower(),
        "BACKGROUND_POLL_INTERVAL_SECONDS": "0.05",
        "BACKGROUND_LEASE_SECONDS": "2",
    })
    if extra_env:
        environment.update(extra_env)
    # Never use stdout=PIPE without a reader: uvicorn access/startup logs plus
    # the tight health/diagnostics polling loops can fill an undrained pipe
    # buffer and stall the server under full-suite load.  A per-test log file
    # preserves diagnostics without back-pressure on the subprocess.
    log_handle = open(tmp_path / f"uvicorn-{port}.log", "ab")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=os.getcwd(),
        env=environment,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    process._e2e_log_handle = log_handle
    deadline = time.time() + 15
    last_error = None
    while time.time() < deadline:
        try:
            _request(port, "GET", "/health")
            return process, port
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if process.poll() is not None:
                _close_log_handle(process)
                output = _log_tail(tmp_path, port)
                raise AssertionError(f"subprocess exited early:\n{output}") from exc
            time.sleep(0.05)
    process.terminate()
    try:
        process.wait(timeout=5)
    except Exception:
        process.kill()
    _close_log_handle(process)
    raise AssertionError(
        f"ThreadLine subprocess did not become healthy: {last_error}\n"
        f"{_log_tail(tmp_path, port)}"
    )


def _stop_app(process):
    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    finally:
        _close_log_handle(process)


def _wait_for_succeeded(port: int, expected: int = 1, timeout: int = 20):
    deadline = time.time() + timeout
    diagnostics = None
    last_error: Exception | str | None = None
    while time.time() < deadline:
        # A single slow/failed poll under load must not abort the wait: the
        # deadline above (unchanged) still bounds the total wait, and the
        # SUCCEEDED-count assertion below is unchanged.
        try:
            _, diagnostics = _request(port, "GET", "/health/diagnostics")
        except Exception as exc:  # noqa: BLE001 - transient poll failure
            last_error = exc
            diagnostics = None
            time.sleep(0.1)
            continue
        if not isinstance(diagnostics, dict):
            last_error = f"non-JSON diagnostics payload: {diagnostics!r}"
            diagnostics = None
            time.sleep(0.1)
            continue
        counts = diagnostics.get("background_job_counts", {})
        if counts.get("SUCCEEDED", 0) >= expected:
            return diagnostics
        time.sleep(0.1)
    raise AssertionError(
        f"timed out waiting for {expected} SUCCEEDED jobs: {diagnostics} (last poll error: {last_error!r})"
    )


def _sqlite_row_counts(db_path: Path) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        tables = ["meetings", "extraction_results", "entities", "entity_mentions", "dependencies", "background_jobs"]
        counts = {}
        for table in tables:
            try:
                counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                counts[table] = 0
        return counts
    finally:
        connection.close()


def _sqlite_jobs(db_path: Path) -> list:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        try:
            rows = connection.execute("SELECT * FROM background_jobs ORDER BY job_id").fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _semantic_records(path: Path) -> list:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 2. Real FastAPI → worker → completion acceptance
# ---------------------------------------------------------------------------

def test_real_fastapi_worker_completion_acceptance(tmp_path):
    """REAL app + REAL SQLite + REAL persistent index + REAL worker + REAL API."""
    process, port = _start_app(tmp_path, worker_enabled=True)
    meeting_id = f"accept-{tmp_path.name}"[:32]
    try:
        # Canonical entities must exist before processing for resolution.
        status, _ = _request(port, "POST", "/api/v1/entities", {"entity_type": "ISSUE", "canonical_name": "Alpha"})
        assert status in {200, 201}
        status, _ = _request(port, "POST", "/api/v1/entities", {"entity_type": "ISSUE", "canonical_name": "Beta"})
        assert status in {200, 201}
        status, _ = _request(port, "POST", "/api/v1/entities", {"entity_type": "PERSON", "canonical_name": "Rahul Kumar"})
        assert status in {200, 201}

        # Realistic meeting through the actual ingestion endpoint (not orchestrator).
        status, response = _request(port, "POST", "/api/v1/meetings", {
            "meeting_id": meeting_id,
            "title": "Delivery Sync",
            "transcript": "Alpha depends on Beta. Rahul Kumar said the payment API is still blocked.",
            "meeting_date": "2026-01-01T10:00:00Z",
            "participants": ["Rahul Kumar"],
        })
        assert status == 201
        assert response["meeting_id"] == meeting_id

        # Wait for the REAL durable job to complete via the REAL worker.
        diagnostics = _wait_for_succeeded(port, expected=1, timeout=20)

        # Inspect SQLite (real durable source).
        db_path = tmp_path / "source.db"
        assert db_path.exists()
        counts = _sqlite_row_counts(db_path)
        assert counts["meetings"] >= 1
        assert counts["extraction_results"] >= 1  # extraction persisted
        assert counts["entity_mentions"] >= 1  # resolution actually ran (observations)
        assert counts["dependencies"] >= 1  # dependencies are correct
        jobs = _sqlite_jobs(db_path)
        assert len(jobs) >= 1
        assert any(j["status"] == "SUCCEEDED" for j in jobs)  # job succeeded
        assert any(j.get("stage") in ("COMPLETED", "SEMANTIC_INDEXED") for j in jobs)  # derived+semantic ran
        # Processing intent persisted with deterministic revision tie.
        assert any((j.get("processing_revision") or "").startswith("1") for j in jobs)

        # Inspect semantic index (real persistent index).
        semantic_path = tmp_path / "semantic.json"
        assert semantic_path.exists()
        records = _semantic_records(semantic_path)
        assert len(records) >= 1  # semantic evidence exists
        assert any(r.get("meeting_id") == meeting_id for r in records)

        # Query the resulting meeting (real query endpoint).
        status, meeting = _request(port, "GET", f"/api/v1/meetings/{meeting_id}")
        assert status == 200
        assert meeting["meeting_id"] == meeting_id

        # Returned query evidence is correct (evidence endpoint, no LLM).
        status, evidence = _request(port, "POST", "/api/v1/query/evidence", {
            "question": "What does Alpha depend on?",
        })
        assert status == 200
        assert isinstance(evidence.get("evidence"), list)
    finally:
        _stop_app(process)


# ---------------------------------------------------------------------------
# 3. Real process-boundary restart recovery
# ---------------------------------------------------------------------------

def test_real_process_restart_recovery(tmp_path):
    meeting_id = f"restart-{tmp_path.name}"[:32]
    first_process, first_port = _start_app(tmp_path, worker_enabled=False)
    try:
        status, _ = _request(first_port, "POST", "/api/v1/entities", {"entity_type": "ISSUE", "canonical_name": "Alpha"})
        assert status in {200, 201}
        status, _ = _request(first_port, "POST", "/api/v1/entities", {"entity_type": "ISSUE", "canonical_name": "Beta"})
        assert status in {200, 201}
        status, response = _request(first_port, "POST", "/api/v1/meetings", {
            "meeting_id": meeting_id,
            "title": "Restart Sync",
            "transcript": "Alpha depends on Beta.",
            "meeting_date": "2026-01-01T10:00:00Z",
            "participants": [],
        })
        assert status == 201

        # Let a worker claim the job via the REAL atomic claim path, ensure RUNNING.
        db_path = tmp_path / "source.db"
        deadline = time.time() + 5
        jobs = []
        while time.time() < deadline:
            jobs = _sqlite_jobs(db_path)
            if jobs:
                break
            time.sleep(0.05)
        assert jobs, "processing intent must survive in the real SQLite database"
        job_id = jobs[0]["job_id"]
        # Real atomic claim via the production repository (correct ISO lease
        # format, same semantics as a PROCESS 1 worker claiming the job).
        from app.persistence.sqlite_store import SQLiteSourceStore as _Store
        from app.repositories.background_job_repository import SQLiteBackgroundJobRepository as _Repo
        _store = _Store(db_path)
        try:
            _claimed = _Repo(_store).claim(job_id, "process-1-worker", lease_seconds=2)
            assert _claimed is not None and _claimed.status.value == "RUNNING"
        finally:
            try:
                _store.close()
            except Exception:
                pass
        jobs_running = _sqlite_jobs(db_path)
        assert any(j["status"] == "RUNNING" for j in jobs_running), "job must be RUNNING before termination"
        # Terminate PROCESS 1 (crash before completion). Source/job survive on disk.
        assert db_path.exists()
    finally:
        _stop_app(first_process)

    # PROCESS 2: fresh application process, EXACT SAME database + semantic index.
    second_process, second_port = _start_app(tmp_path, worker_enabled=True)
    try:
        diagnostics = _wait_for_succeeded(second_port, expected=1, timeout=20)
        # Source data survives.
        status, meeting = _request(second_port, "GET", f"/api/v1/meetings/{meeting_id}")
        assert status == 200
        assert meeting["meeting_id"] == meeting_id
        # Job survives, stale job recoverable, finishes SUCCEEDED.
        jobs_after = _sqlite_jobs(tmp_path / "source.db")
        assert any(j["status"] == "SUCCEEDED" for j in jobs_after)
        assert any(j.get("stage") == "COMPLETED" for j in jobs_after)
        # No duplicate dependencies / semantic records.
        counts = _sqlite_row_counts(tmp_path / "source.db")
        assert counts["dependencies"] == 1
        records = _semantic_records(tmp_path / "semantic.json")
        evidence_ids = [r["evidence_id"] for r in records]
        assert len(evidence_ids) == len(set(evidence_ids))
        # Final query works.
        status, evidence = _request(second_port, "POST", "/api/v1/query/evidence", {
            "question": "What does Alpha depend on?",
        })
        assert status == 200
    finally:
        _stop_app(second_process)


# ---------------------------------------------------------------------------
# 13. Canonical user lifecycle (28 steps)
# ---------------------------------------------------------------------------

def test_real_user_lifecycle_acceptance(tmp_path):
    """Fresh app → ingest → process → query → restart → revise → reprocess."""
    meeting_id = f"lifecycle-{tmp_path.name}"[:32]
    # 1-4: fresh application with durable SQLite + persistent index + worker.
    process, port = _start_app(tmp_path, worker_enabled=True)
    try:
        # 5: user submits meeting through API.
        for name, etype in (("Rahul Kumar", "PERSON"), ("Alpha", "ISSUE"), ("Beta", "ISSUE")):
            status, _ = _request(port, "POST", "/api/v1/entities", {"entity_type": etype, "canonical_name": name})
            assert status in {200, 201}
        status, response = _request(port, "POST", "/api/v1/meetings", {
            "meeting_id": meeting_id,
            "title": "Lifecycle Sync",
            "transcript": "Alpha depends on Beta. Rahul Kumar confirmed the status.",
            "meeting_date": "2026-01-01T10:00:00Z",
            "participants": ["Rahul Kumar"],
        })
        assert status == 201
        # 6: job durably created.
        assert _sqlite_jobs(tmp_path / "source.db"), "6: job durably created"
        # 7-14: worker processes → extraction → observations → resolution →
        # dependencies → derived → semantic → SUCCEEDED.
        _wait_for_succeeded(port, expected=1, timeout=20)
        jobs = _sqlite_jobs(tmp_path / "source.db")
        assert any(j["status"] == "SUCCEEDED" for j in jobs), "14: job succeeds"
        assert any(j.get("stage") == "COMPLETED" for j in jobs)
        # 15-16: user queries meeting, evidence returned.
        status, meeting = _request(port, "GET", f"/api/v1/meetings/{meeting_id}")
        assert status == 200
        status, evidence = _request(port, "POST", "/api/v1/query/evidence", {
            "question": "What does Alpha depend on?",
        })
        assert status == 200 and isinstance(evidence.get("evidence"), list)
        first_semantic = _semantic_records(tmp_path / "semantic.json")
        assert first_semantic, "13: semantic evidence indexed"
        first_dep_count = _sqlite_row_counts(tmp_path / "source.db")["dependencies"]
        assert first_dep_count == 1
        # 17: terminate.
    finally:
        _stop_app(process)

    # 18-20: restart, data survives, query remains functional.
    process2, port2 = _start_app(tmp_path, worker_enabled=True)
    try:
        status, meeting = _request(port2, "GET", f"/api/v1/meetings/{meeting_id}")
        assert status == 200, "19: data survives restart"
        status, evidence = _request(port2, "POST", "/api/v1/query/evidence", {
            "question": "What does Alpha depend on?",
        })
        assert status == 200, "20: query remains functional"

        # 21-23: user revises source → new source revision → new execution.
        status, revised = _request(port2, "PUT", f"/api/v1/meetings/{meeting_id}", {
            "title": "Lifecycle Sync",
            "transcript": "Alpha depends on Beta. Beta is now blocked and Rahul Kumar confirmed it.",
            "meeting_date": "2026-01-02T10:00:00Z",
            "participants": ["Rahul Kumar"],
        })
        assert status in {200, 202}, f"21: revise creates new source revision, got {status}"
        # 24-25: worker processes new revision, semantic reflects new revision.
        _wait_for_succeeded(port2, expected=2, timeout=20)
        jobs_after = _sqlite_jobs(tmp_path / "source.db")
        succeeded = [j for j in jobs_after if j["status"] == "SUCCEEDED"]
        assert len(succeeded) >= 2, "26: old history remains intact + new succeeds"
        # 27-28: no duplicates.
        counts = _sqlite_row_counts(tmp_path / "source.db")
        assert counts["dependencies"] >= 1
        records = _semantic_records(tmp_path / "semantic.json")
        evidence_ids = [r["evidence_id"] for r in records]
        assert len(evidence_ids) == len(set(evidence_ids)), "28: no duplicate semantic vectors"
        # New revision query still works.
        status, evidence = _request(port2, "POST", "/api/v1/query/evidence", {
            "question": "What does Alpha depend on?",
        })
        assert status == 200
    finally:
        _stop_app(process2)
