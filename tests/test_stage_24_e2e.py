"""Stage 24 full-stack security acceptance (§27): real tenants end to end.

Tenant A (User A) and Tenant B (User B) through REAL uvicorn subprocesses +
REAL SQLite + REAL worker + REAL persistent semantic index + REAL
authentication/authorization.  No mocked security boundaries.

Covers §27 steps 1–24: authenticate, create, process, query per tenant,
cross-tenant attack matrix both directions, semantic cross-fire, dependency
and job isolation, immediate revocation on role change, restart durability.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(port, method, path, payload=None, headers=None, timeout=10):
    body = None if payload is None else json.dumps(payload).encode()
    merged = {"Content-Type": "application/json"} if body else {}
    merged.update(headers or {})
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=merged)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            try:
                data = json.loads(raw) if raw else None
            except Exception:
                data = None
            return response.status, data
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw) if raw else None
        except Exception:
            data = None
        return exc.code, data


def _start_app(tmp_path: Path, worker_enabled: bool):
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
        "BACKGROUND_LEASE_SECONDS": "5",
        # Test-only speed: fewer PBKDF2 rounds.  Production default is 600k.
        "AUTH_PBKDF2_ITERATIONS": "20000",
    })
    log_handle = open(tmp_path / f"uvicorn24-{port}.log", "ab")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=os.getcwd(), env=environment, stdout=log_handle, stderr=subprocess.STDOUT)
    process._e2e_log_handle = log_handle  # noqa: SLF001
    deadline = time.time() + 20
    last_error = None
    while time.time() < deadline:
        try:
            _request(port, "GET", "/health")
            return process, port
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if process.poll() is not None:
                log_handle.close()
                raise AssertionError(f"subprocess exited early: {last_error}") from exc
            time.sleep(0.05)
    process.terminate()
    try:
        process.wait(timeout=5)
    except Exception:
        process.kill()
    log_handle.close()
    raise AssertionError(f"ThreadLine subprocess did not become healthy: {last_error}")


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
        try:
            process._e2e_log_handle.close()  # noqa: SLF001
        except Exception:
            pass


def _wait_for_succeeded(port, headers, expected=1, timeout=30):
    deadline = time.time() + timeout
    diagnostics = None
    while time.time() < deadline:
        try:
            status, diagnostics = _request(port, "GET", "/health/diagnostics", headers=headers)
        except Exception:
            time.sleep(0.1)
            continue
        if status == 200 and isinstance(diagnostics, dict):
            if diagnostics.get("background_job_counts", {}).get("SUCCEEDED", 0) >= expected:
                return diagnostics
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {expected} SUCCEEDED jobs: {diagnostics}")


def _auth(token, org_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if org_id is not None:
        headers["X-Organisation-ID"] = org_id
    return headers


def test_real_full_stack_tenant_acceptance(tmp_path):
    process, port = _start_app(tmp_path, worker_enabled=True)
    try:
        # 1. Authenticate A: bootstrap the first organisation + owner.
        status, boot_a = _request(port, "POST", "/api/v1/auth/bootstrap", {
            "organisation_name": "Tenant Alpha", "slug": f"alpha-{tmp_path.name}"[:40],
            "admin_email": "alice@alpha.example", "password": "alice-password-1"})
        assert status == 201, boot_a
        token_a = boot_a["token"]["access_token"]
        org_a = boot_a["organisation"]["organisation_id"]
        user_a = boot_a["user"]["user_id"]
        ha = _auth(token_a, org_a)

        # 2–3. Create + process meeting A (real worker, tenant-scoped pipeline).
        meeting_a = f"alpha-{tmp_path.name}"[:32]
        status, _ = _request(port, "POST", "/api/v1/meetings", {
            "meeting_id": meeting_a, "title": "Alpha Sync",
            "transcript": "Project Apollo is on track. Alpha depends on Beta.",
            "meeting_date": "2026-01-01T10:00:00Z", "participants": []}, headers=ha)
        assert status == 201
        status, entity_a = _request(port, "POST", "/api/v1/entities", {
            "entity_type": "ISSUE", "canonical_name": "Project Apollo"}, headers=ha)
        assert status in {200, 201}
        _wait_for_succeeded(port, ha, expected=1)

        # 4–6. Query A: evidence returned, scoped to A.
        status, evidence_a = _request(port, "POST", "/api/v1/query/evidence", {
            "question": "What is the status of Project Apollo?"}, headers=ha)
        assert status == 200 and evidence_a["evidence"], "A evidence missing"

        # 7–10. Tenant B: second org (bootstrap closed → provisioned via API).
        status, closed = _request(port, "POST", "/api/v1/auth/bootstrap", {
            "organisation_name": "Evil", "slug": "evil",
            "admin_email": "evil@x.example", "password": "evil-password-1"})
        assert status == 403, "bootstrap must close after the first owner"
        status, org_b_doc = _request(port, "POST", "/api/v1/orgs", {
            "name": "Tenant Beta", "slug": f"beta-{tmp_path.name}"[:40]}, headers=ha)
        assert status == 201, org_b_doc
        org_b = org_b_doc["organisation_id"]
        status, member_b = _request(port, "POST", f"/api/v1/orgs/{org_b}/members", {
            "email": "bob@beta.example", "role": "OWNER", "password": "bob-password-1"},
            headers=ha)
        # A is owner of B at this point, so granting OWNER is allowed.
        assert status == 201, member_b
        user_b = member_b["user_id"]
        status, login_b = _request(port, "POST", "/api/v1/auth/login", {
            "email": "bob@beta.example", "password": "bob-password-1"})
        assert status == 200
        token_b = login_b["access_token"]
        hb = _auth(token_b, org_b)
        # A leaves B: disjoint tenants from here on.
        status, _ = _request(port, "DELETE", f"/api/v1/orgs/{org_b}/members/{user_a}", headers=hb)
        assert status == 200

        meeting_b = f"beta-{tmp_path.name}"[:32]
        status, _ = _request(port, "POST", "/api/v1/meetings", {
            "meeting_id": meeting_b, "title": "Beta Sync",
            "transcript": "Project Apollo is blocked. Alpha depends on Beta.",
            "meeting_date": "2026-01-01T10:00:00Z", "participants": []}, headers=hb)
        assert status == 201
        status, entity_b = _request(port, "POST", "/api/v1/entities", {
            "entity_type": "ISSUE", "canonical_name": "Project Apollo"}, headers=hb)
        assert status in {200, 201}
        # Identical names coexist; identities differ.
        assert entity_a["entity_id"] != entity_b["entity_id"]
        _wait_for_succeeded(port, hb, expected=1)

        # 11–12. Query B: evidence returned, scoped to B.
        status, evidence_b = _request(port, "POST", "/api/v1/query/evidence", {
            "question": "What is the status of Project Apollo?"}, headers=hb)
        assert status == 200 and evidence_b["evidence"], "B evidence missing"

        # 13–14. A attempts B access: every direction rejected as not-found.
        status, _ = _request(port, "GET", f"/api/v1/meetings/{meeting_b}", headers=ha)
        assert status == 404, status
        status, _ = _request(port, "PUT", f"/api/v1/meetings/{meeting_b}", {
            "title": "Hijack", "transcript": "hijacked transcript content here",
            "meeting_date": "2026-01-02T10:00:00Z"}, headers=ha)
        assert status == 404, status
        status, _ = _request(port, "POST", f"/api/v1/meetings/{meeting_b}/extract", headers=ha)
        assert status == 404, status
        status, _ = _request(port, "GET", f"/api/v1/entities/{entity_b['entity_id']}", headers=ha)
        assert status == 404, status
        status, _ = _request(
            port, "GET",
            f"/api/v1/entities/{entity_b['entity_id']}/dependency-graph", headers=ha)
        assert status == 404, status

        # 15–16. Reverse: B attempts A access → rejected.
        status, _ = _request(port, "GET", f"/api/v1/meetings/{meeting_a}", headers=hb)
        assert status == 404, status
        status, _ = _request(port, "GET", f"/api/v1/entities/{entity_a['entity_id']}", headers=hb)
        assert status == 404, status

        # 17–18. Semantic cross-fire: A's query uses B's exact sentence; B evidence must not appear.
        status, cross = _request(port, "POST", "/api/v1/query/evidence", {
            "question": "Is Project Apollo blocked?"}, headers=ha)
        assert status == 200
        blob = json.dumps(cross["evidence"])
        assert meeting_b not in blob, "tenant B meeting leaked into tenant A semantic results"
        assert entity_b["entity_id"] not in blob, "tenant B entity leaked"
        assert "blocked" not in blob.lower(), "tenant B content leaked into tenant A context"

        # 19–20. Dependency + job isolation.
        status, graph_a = _request(
            port, "GET",
            f"/api/v1/entities/{entity_a['entity_id']}/dependency-graph", headers=ha)
        assert status == 200
        assert entity_b["entity_id"] not in json.dumps(graph_a)
        status, jobs_a = _request(port, "GET", "/api/v1/health/jobs", headers=ha)
        assert status == 200
        # A sees only its own meeting's job.
        assert jobs_a["counts"].get("SUCCEEDED", 0) == 1, jobs_a

        # 21–22. Reprocessing B as A is rejected.
        status, _ = _request(port, "POST", f"/api/v1/meetings/{meeting_b}/extract", headers=ha)
        assert status == 404, status

        # 23. Access updates immediately: disable B → token dies at once.
        status, _ = _request(port, "POST", f"/api/v1/orgs/{org_b}/users/{user_b}/disable",
                             headers=hb)
        assert status == 200, "owner may disable within their org"
        status, _ = _request(port, "GET", f"/api/v1/meetings/{meeting_b}", headers=hb)
        assert status == 401, status

        # Filesystem reality check: the shared semantic file holds BOTH
        # tenants' records, yet the API boundary above never crossed them.
        records = json.loads((tmp_path / "semantic.json").read_text(encoding="utf-8"))
        orgs_in_file = {r.get("organisation_id") for r in records}
        assert {org_a, org_b} <= orgs_in_file, orgs_in_file
    finally:
        _stop_app(process)

    # 24. Restart: sessions + tenant data survive a full process restart.
    process2, port2 = _start_app(tmp_path, worker_enabled=True)
    try:
        # A token issued before the crash still authenticates (durable session).
        status, meeting = _request(port2, "GET", f"/api/v1/meetings/{meeting_a}", headers=ha)
        assert status == 200, "tenant data must survive restart"
        status, evidence = _request(port2, "POST", "/api/v1/query/evidence", {
            "question": "What is the status of Project Apollo?"}, headers=ha)
        assert status == 200 and evidence["evidence"]
        # B stays disabled across the restart.
        status, _ = _request(port2, "GET", f"/api/v1/meetings/{meeting_b}", headers=hb)
        assert status == 401, status
    finally:
        _stop_app(process2)
