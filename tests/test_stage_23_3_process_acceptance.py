import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request



def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(port: int, method: str, path: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read())


def _start_app(tmp_path, worker_enabled: bool):
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
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=os.getcwd(),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            _request(port, "GET", "/health")
            return process, port
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.05)
    process.terminate()
    process.wait(timeout=5)
    raise AssertionError("ThreadLine subprocess did not become healthy")


def test_real_http_restart_pipeline_persists_and_completes(tmp_path):
    first_process, first_port = _start_app(tmp_path, worker_enabled=False)
    meeting_id = f"acceptance-{tmp_path.name}"
    try:
        try:
            status, response = _request(first_port, "POST", "/api/v1/meetings", {
                "meeting_id": meeting_id,
                "title": "Payments",
                "transcript": "Rahul Kumar said the payment API is still blocked.",
                "meeting_date": "2026-01-01T10:00:00Z",
                "participants": ["Rahul Kumar"],
            })
        except Exception as exc:
            detail = exc.read().decode(errors="replace") if isinstance(exc, urllib.error.HTTPError) else str(exc)
            first_process.terminate()
            output = first_process.communicate(timeout=5)[0].decode(errors="replace")
            raise AssertionError(f"{detail}\n{output}") from exc
        assert status == 201
        assert response["meeting_id"] == meeting_id

        status, entity = _request(first_port, "POST", "/api/v1/entities", {
            "entity_type": "PERSON",
            "canonical_name": "Rahul Kumar",
        })
        assert status in {200, 201}
        _request(first_port, "POST", "/api/v1/entities/mentions", {
            "entity_type": "PERSON",
            "text": "Rahul Kumar",
            "meeting_id": meeting_id,
            "source_text": "Rahul Kumar said the payment API is still blocked.",
        })
    finally:
        first_process.terminate()
        first_process.wait(timeout=5)

    second_process, second_port = _start_app(tmp_path, worker_enabled=True)
    try:
        deadline = time.time() + 15
        job = None
        while time.time() < deadline:
            try:
                _, diagnostics = _request(second_port, "GET", "/health/diagnostics")
            except Exception as exc:
                second_process.terminate()
                output = second_process.communicate(timeout=5)[0].decode(errors="replace")
                raise AssertionError(output) from exc
            if diagnostics["background_job_counts"].get("SUCCEEDED", 0) == 1:
                job = diagnostics
                break
            time.sleep(0.1)
        assert job is not None

        status, meeting = _request(second_port, "GET", f"/api/v1/meetings/{meeting_id}")
        assert status == 200
        assert meeting["meeting_id"] == meeting_id
        assert (tmp_path / "source.db").exists()
        assert (tmp_path / "semantic.json").exists()
    finally:
        second_process.terminate()
        second_process.wait(timeout=5)