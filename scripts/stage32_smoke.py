"""Stage 32 release smoke test: fresh durable boot over the real uvicorn path.

Boots uvicorn with SOURCE_REPOSITORY_BACKEND=database and
BACKGROUND_WORKER_ENABLED=true against a brand-new temp database, then:
  /health -> bootstrap   -> login        -> ingest meeting
  -> worker SUCCEEDED   -> query        -> restart durability (session + data)
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "Scripts" / "python.exe"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _request(port, method, path, payload=None, headers=None, timeout=15):
    body = None if payload is None else json.dumps(payload).encode()
    merged = {"Content-Type": "application/json"} if body else {}
    merged.update(headers or {})
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                data = json.loads(raw) if raw else None
            except Exception:
                data = None
            return resp.status, data
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw) if raw else None
        except Exception:
            data = None
        return exc.code, data


def _wait(fn, timeout=30):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            result = fn()
            if result is not None:
                return result
        except Exception as exc:  # server not up yet
            last = exc
        time.sleep(0.25)
    raise RuntimeError(f"timed out: {last}")


def _boot(workdir, port, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    proc = subprocess.Popen(
        [str(PY), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=workdir, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _wait(lambda: _request(port, "GET", "/health")[0] == 200 or None)
    return proc


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        db = tmp / "threadline.db"
        shared_env = {
            "SOURCE_REPOSITORY_BACKEND": "database",
            "SOURCE_DATABASE_PATH": str(db),
            "BACKGROUND_WORKER_ENABLED": "true",
            "AUTH_OPEN_BOOTSTRAP": "true",
            "EXTRACTION_PROVIDER": "fake",
            "NL_PROVIDER": "fake",
        }

        # 1. Fresh boot: migrations run, worker starts, /health is green.
        port = _free_port()
        proc = _boot(REPO, port, shared_env)
        assert db.exists(), "SQLite database was not created"
        print("boot:                OK (fresh DB created, /health green)")

        # 2. Bootstrap the first owner.
        status, boot = _request(port, "POST", "/api/v1/auth/bootstrap", {
            "organisation_name": "Smoke Lab", "slug": "smoke-lab",
            "admin_email": "admin@smoke.example", "password": "CorrectHorse42!"})
        assert status in (200, 201), (status, boot)
        org_id = boot["organisation"]["organisation_id"]
        print("bootstrap:           OK (organisation " + org_id + ")")

        # 3. Login.
        status, login = _request(port, "POST", "/api/v1/auth/login", {
            "email": "admin@smoke.example", "password": "CorrectHorse42!"})
        assert status == 200, (status, login)
        token = login["access_token"]
        ha = {"Authorization": f"Bearer {token}", "X-Organisation-ID": org_id}
        print("login + session:     OK (opaque token)")

        # 4. Ingest a meeting; the worker processes it durably.
        status, meeting = _request(port, "POST", "/api/v1/meetings", {
            "title": "Release Sync",
            "transcript": "Rahul reported the API is still unstable. "
                          "Priya asked him to investigate before Friday.",
            "meeting_date": "2026-09-15T10:00:00Z",
            "participants": ["Rahul Kumar"]}, headers=ha)
        assert status == 201, (status, meeting)
        meeting_id = meeting["meeting_id"]
        status, jobs = _request(port, "GET", "/api/v1/health/jobs", headers=ha)
        assert status == 200

        def _succeeded():
            _, j = _request(port, "GET", "/api/v1/health/jobs", headers=ha)
            return j["counts"].get("SUCCEEDED", 0) >= 1 or None

        _wait(_succeeded, timeout=30)
        _, jobs = _request(port, "GET", "/api/v1/health/jobs", headers=ha)
        assert jobs["counts"].get("SUCCEEDED", 0) == 1, jobs
        print("worker:              OK (meeting SUCCEEDED durably)")

        # 5. Query works end to end.
        status, data = _request(port, "POST", "/api/v1/query/evidence", {
            "question": "What is the status of the payment API?"}, headers=ha)
        assert status == 200, (status, data)
        print("query:               OK (durable query path green)")

        # 6. Restart: session + data survive.
        proc.terminate(); proc.wait(timeout=15)
        proc2 = _boot(REPO, port, shared_env)
        status, meeting2 = _request(port, "GET", f"/api/v1/meetings/{meeting_id}", headers=ha)
        assert status == 200, (status, meeting2)
        print("restart durability:  OK (session + meeting survived)")
        proc2.terminate(); proc2.wait(timeout=15)

    print("\nALL RELEASE SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()