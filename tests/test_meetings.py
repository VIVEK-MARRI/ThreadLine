"""Tests for the Meeting Ingestion API.

Covers the three behaviours we care about today:
  1. Successful meeting ingestion (POST /api/v1/meetings)
  2. Successful meeting retrieval (GET /api/v1/meetings/{id})
  3. 404 response for an unknown meeting ID
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> TestClient:
    """Return a fresh TestClient for each test.

    NOTE: The in-memory repository is a module-level singleton, so state
    persists between tests within a session.  Prefix each test's data with
    unique values (or use parametrize) to remain isolated.
    """
    return TestClient(app)


VALID_MEETING_PAYLOAD = {
    "title": "Payment Integration Weekly Sync",
    "transcript": (
        "Rahul reported that the payment provider API is still unstable. "
        "Priya asked him to investigate the issue before Friday."
    ),
    "meeting_date": "2026-08-23T10:00:00Z",
    "participants": ["Rahul Kumar", "Priya Sharma"],
}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def test_health_check(client: TestClient) -> None:
    """GET /health should return 200 with status 'healthy'."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def test_ingest_meeting_returns_201_with_id_and_status(client: TestClient) -> None:
    """A valid POST should return 201 with a meeting_id and status='ingested'."""
    response = client.post("/api/v1/meetings", json=VALID_MEETING_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert "meeting_id" in body
    assert body["status"] == "ingested"
    assert len(body["meeting_id"]) > 0


def test_ingest_meeting_without_participants(client: TestClient) -> None:
    """participants is optional — omitting it should still return 201."""
    payload = {k: v for k, v in VALID_MEETING_PAYLOAD.items() if k != "participants"}
    response = client.post("/api/v1/meetings", json=payload)
    assert response.status_code == 201
    assert response.json()["status"] == "ingested"


def test_ingest_meeting_blank_title_returns_422(client: TestClient) -> None:
    """A blank title should be rejected with HTTP 422."""
    payload = {**VALID_MEETING_PAYLOAD, "title": "   "}
    response = client.post("/api/v1/meetings", json=payload)
    assert response.status_code == 422


def test_ingest_meeting_blank_transcript_returns_422(client: TestClient) -> None:
    """A blank transcript should be rejected with HTTP 422."""
    payload = {**VALID_MEETING_PAYLOAD, "transcript": ""}
    response = client.post("/api/v1/meetings", json=payload)
    assert response.status_code == 422


def test_ingest_meeting_invalid_date_returns_422(client: TestClient) -> None:
    """An invalid meeting_date should be rejected with HTTP 422."""
    payload = {**VALID_MEETING_PAYLOAD, "meeting_date": "not-a-date"}
    response = client.post("/api/v1/meetings", json=payload)
    assert response.status_code == 422


def test_ingest_meeting_empty_participant_name_returns_422(client: TestClient) -> None:
    """A participant list containing an empty string should be rejected."""
    payload = {**VALID_MEETING_PAYLOAD, "participants": ["Rahul Kumar", ""]}
    response = client.post("/api/v1/meetings", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def test_get_meeting_returns_original_data(client: TestClient) -> None:
    """After ingestion, GET should return the same data that was posted."""
    # Ingest
    post_response = client.post("/api/v1/meetings", json=VALID_MEETING_PAYLOAD)
    assert post_response.status_code == 201
    meeting_id = post_response.json()["meeting_id"]

    # Retrieve
    get_response = client.get(f"/api/v1/meetings/{meeting_id}")
    assert get_response.status_code == 200
    body = get_response.json()

    assert body["meeting_id"] == meeting_id
    assert body["title"] == VALID_MEETING_PAYLOAD["title"]
    assert body["transcript"] == VALID_MEETING_PAYLOAD["transcript"]
    assert body["participants"] == VALID_MEETING_PAYLOAD["participants"]
    assert "ingested_at" in body


def test_get_meeting_not_found_returns_404(client: TestClient) -> None:
    """Requesting a non-existent meeting ID should return HTTP 404."""
    response = client.get("/api/v1/meetings/does-not-exist-00000")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_each_ingested_meeting_gets_unique_id(client: TestClient) -> None:
    """Two identical POST requests should yield two distinct meeting IDs."""
    r1 = client.post("/api/v1/meetings", json=VALID_MEETING_PAYLOAD)
    r2 = client.post("/api/v1/meetings", json=VALID_MEETING_PAYLOAD)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["meeting_id"] != r2.json()["meeting_id"]
