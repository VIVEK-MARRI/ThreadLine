"""Tests for the Information Extraction pipeline.

All tests use FakeExtractionProvider — no real LLM calls are made.
The application is configured with EXTRACTION_PROVIDER=fake so that
the TestClient uses the fake provider without needing an API key.

Test coverage:
  1. Successful extraction returns 200 with all four categories.
  2. Extraction for a nonexistent meeting returns 404.
  3. ExtractionResult Pydantic schemas validate correctly.
  4. Optional fields (owner, deadline, severity) can be null — never fabricated.
  5. Every extracted item carries supporting evidence.
  6. Provider failure surfaces as a 503 (not a silent empty result).
  7. Extraction result is persisted (GET returns last result).
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.extraction.base import ExtractionError
from app.extraction.fake_provider import FakeExtractionProvider
from app.models.extraction import (
    Decision,
    Evidence,
    ExtractionResult,
    Issue,
    Risk,
    Task,
)
from app.repositories.extraction_repository import InMemoryExtractionRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.services.extraction_service import ExtractionService, MeetingNotFoundError


# ---------------------------------------------------------------------------
# Canonical test fixtures
# ---------------------------------------------------------------------------

MEETING_PAYLOAD = {
    "title": "Payment Integration Weekly Sync",
    "transcript": (
        "Rahul reported that the payment provider API is still unstable. "
        "Priya asked him to investigate the issue before Friday. "
        "The team agreed to delay the release if the issue is not resolved."
    ),
    "meeting_date": "2026-08-23T10:00:00Z",
    "participants": ["Rahul Kumar", "Priya Sharma"],
}


def _make_rich_result(meeting_id: str) -> ExtractionResult:
    """Build a representative ExtractionResult for use in tests."""
    return ExtractionResult(
        meeting_id=meeting_id,
        extracted_at=datetime(2026, 8, 23, 22, 5, 0, tzinfo=timezone.utc),
        issues=[
            Issue(
                description="Payment provider API is unstable.",
                evidence=Evidence(
                    source_text=(
                        "Rahul reported that the payment provider API is still unstable."
                    )
                ),
            )
        ],
        tasks=[
            Task(
                description="Investigate the payment provider issue.",
                owner="Rahul",
                deadline="Friday",
                evidence=Evidence(
                    source_text="Priya asked him to investigate the issue before Friday."
                ),
            )
        ],
        decisions=[
            Decision(
                description="Delay the release if the issue is not resolved.",
                evidence=Evidence(
                    source_text=(
                        "The team agreed to delay the release if the issue is not resolved."
                    )
                ),
            )
        ],
        risks=[],
    )


def _make_null_fields_result(meeting_id: str) -> ExtractionResult:
    """Result where all optional fields are explicitly None (not fabricated)."""
    return ExtractionResult(
        meeting_id=meeting_id,
        extracted_at=datetime(2026, 8, 23, 22, 5, 0, tzinfo=timezone.utc),
        tasks=[
            Task(
                description="Look into the API.",
                owner=None,       # not explicitly named in transcript
                deadline=None,    # not explicitly stated
                evidence=Evidence(source_text="Rahul said he would look into the API."),
            )
        ],
        issues=[],
        decisions=[],
        risks=[
            Risk(
                description="API instability may affect release.",
                severity=None,    # severity not explicitly stated
                evidence=Evidence(source_text="The payment provider API is still unstable."),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Helper: build a TestClient backed by a specific fake provider
# ---------------------------------------------------------------------------

def _client_with_provider(fake_provider: FakeExtractionProvider) -> TestClient:
    """Create a TestClient with a specific fake extraction provider injected.

    We patch the meetings router's dependency override at the FastAPI app level
    so no real LLM calls are ever made.
    """
    from app.main import app
    from app.api.meetings import get_extraction_service

    shared_meeting_repo = InMemoryMeetingRepository()
    shared_extraction_repo = InMemoryExtractionRepository()

    def override_extraction_service() -> ExtractionService:
        return ExtractionService(
            meeting_repository=shared_meeting_repo,
            extraction_repository=shared_extraction_repo,
            provider=fake_provider,
        )

    from app.api.meetings import get_meeting_service

    def override_meeting_service():
        from app.services.meeting_service import MeetingService
        return MeetingService(repository=shared_meeting_repo)

    app.dependency_overrides[get_extraction_service] = override_extraction_service
    app.dependency_overrides[get_meeting_service] = override_meeting_service

    client = TestClient(app)
    return client


# ---------------------------------------------------------------------------
# 1. Successful extraction — all four categories
# ---------------------------------------------------------------------------

def test_extract_meeting_returns_200_with_structured_result() -> None:
    """A valid POST /extract should return 200 with all extracted categories."""
    # We need the meeting_id before building the result, so we use a two-step
    # approach: ingest first, then configure the fake with the real id.
    meeting_id_holder: list[str] = []

    class DeferredFakeProvider(FakeExtractionProvider):
        def extract(self, transcript, meeting_id):
            return _make_rich_result(meeting_id)

    from app.models.extraction import ExtractionResult
    placeholder = ExtractionResult(
        meeting_id="placeholder",
        extracted_at=datetime.now(tz=timezone.utc),
    )
    provider = DeferredFakeProvider(result=placeholder)
    client = _client_with_provider(provider)

    post = client.post("/api/v1/meetings", json=MEETING_PAYLOAD)
    assert post.status_code == 201
    meeting_id = post.json()["meeting_id"]

    response = client.post(f"/api/v1/meetings/{meeting_id}/extract")
    assert response.status_code == 200

    body = response.json()
    assert body["meeting_id"] == meeting_id
    assert "extracted_at" in body

    # Issues
    assert len(body["issues"]) == 1
    assert "payment provider api" in body["issues"][0]["description"].lower()
    assert body["issues"][0]["evidence"]["source_text"] != ""

    # Tasks
    assert len(body["tasks"]) == 1
    task = body["tasks"][0]
    assert task["owner"] == "Rahul"
    assert task["deadline"] == "Friday"
    assert task["evidence"]["source_text"] != ""

    # Decisions
    assert len(body["decisions"]) == 1
    assert "delay" in body["decisions"][0]["description"].lower()

    # Risks — empty in this fixture
    assert body["risks"] == []


# ---------------------------------------------------------------------------
# 2. Nonexistent meeting → 404
# ---------------------------------------------------------------------------

def test_extract_nonexistent_meeting_returns_404() -> None:
    """POSTing to extract on an unknown meeting_id must return 404."""
    from app.models.extraction import ExtractionResult
    placeholder = ExtractionResult(
        meeting_id="placeholder",
        extracted_at=datetime.now(tz=timezone.utc),
    )
    provider = FakeExtractionProvider(result=placeholder)
    client = _client_with_provider(provider)

    response = client.post("/api/v1/meetings/does-not-exist-00000/extract")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. Pydantic schema validation of ExtractionResult
# ---------------------------------------------------------------------------

def test_extraction_result_schema_validates_correctly() -> None:
    """ExtractionResult and its sub-models must accept valid data."""
    result = _make_rich_result("test-meeting-id")
    assert result.meeting_id == "test-meeting-id"
    assert len(result.issues) == 1
    assert len(result.tasks) == 1
    assert len(result.decisions) == 1
    assert len(result.risks) == 0


def test_extraction_result_requires_evidence() -> None:
    """Each extracted item must have an Evidence with non-empty source_text."""
    result = _make_rich_result("test-meeting-id")
    for issue in result.issues:
        assert issue.evidence.source_text
    for task in result.tasks:
        assert task.evidence.source_text
    for decision in result.decisions:
        assert decision.evidence.source_text


def test_extraction_result_rejects_missing_required_fields() -> None:
    """Issue without description or evidence must fail validation."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        Issue(evidence=Evidence(source_text="some text"))  # missing description

    with pytest.raises(pydantic.ValidationError):
        Issue(description="something")  # missing evidence


# ---------------------------------------------------------------------------
# 4. Optional fields remain null — not fabricated
# ---------------------------------------------------------------------------

def test_optional_fields_are_null_when_not_present() -> None:
    """owner, deadline, and severity must be None when not explicitly stated."""
    result = _make_null_fields_result("test-meeting-id")

    task = result.tasks[0]
    assert task.owner is None, "owner must be None when not explicitly named"
    assert task.deadline is None, "deadline must be None when not explicitly stated"

    risk = result.risks[0]
    assert risk.severity is None, "severity must be None when not explicitly stated"


def test_optional_null_fields_serialise_correctly_in_api() -> None:
    """Null optional fields must appear as null in the JSON response."""

    class NullFieldsFakeProvider(FakeExtractionProvider):
        def extract(self, transcript, meeting_id):
            return _make_null_fields_result(meeting_id)

    from app.models.extraction import ExtractionResult
    placeholder = ExtractionResult(
        meeting_id="placeholder",
        extracted_at=datetime.now(tz=timezone.utc),
    )
    provider = NullFieldsFakeProvider(result=placeholder)
    client = _client_with_provider(provider)

    post = client.post("/api/v1/meetings", json=MEETING_PAYLOAD)
    meeting_id = post.json()["meeting_id"]

    response = client.post(f"/api/v1/meetings/{meeting_id}/extract")
    assert response.status_code == 200

    body = response.json()
    task = body["tasks"][0]
    assert task["owner"] is None
    assert task["deadline"] is None

    risk = body["risks"][0]
    assert risk["severity"] is None


# ---------------------------------------------------------------------------
# 5. Evidence is always present on every extracted item
# ---------------------------------------------------------------------------

def test_all_extracted_items_have_evidence() -> None:
    """Every item in the result must have non-empty source_text evidence."""

    class EvidenceCheckFakeProvider(FakeExtractionProvider):
        def extract(self, transcript, meeting_id):
            return _make_rich_result(meeting_id)

    from app.models.extraction import ExtractionResult
    placeholder = ExtractionResult(
        meeting_id="placeholder",
        extracted_at=datetime.now(tz=timezone.utc),
    )
    provider = EvidenceCheckFakeProvider(result=placeholder)
    client = _client_with_provider(provider)

    post = client.post("/api/v1/meetings", json=MEETING_PAYLOAD)
    meeting_id = post.json()["meeting_id"]

    response = client.post(f"/api/v1/meetings/{meeting_id}/extract")
    assert response.status_code == 200
    body = response.json()

    for category in ("issues", "tasks", "decisions", "risks"):
        for item in body[category]:
            assert "evidence" in item, f"Item in {category} missing evidence"
            assert item["evidence"]["source_text"], f"Item in {category} has empty evidence"


# ---------------------------------------------------------------------------
# 6. Provider failure → 503 (not a silent empty result)
# ---------------------------------------------------------------------------

def test_provider_failure_returns_503() -> None:
    """If the extraction provider fails, the API must return 503, not 200."""
    from app.models.extraction import ExtractionResult

    placeholder = ExtractionResult(
        meeting_id="placeholder",
        extracted_at=datetime.now(tz=timezone.utc),
    )
    failing_provider = FakeExtractionProvider(
        result=placeholder,
        raise_on_extract=ExtractionError("Simulated provider failure"),
    )
    client = _client_with_provider(failing_provider)

    post = client.post("/api/v1/meetings", json=MEETING_PAYLOAD)
    assert post.status_code == 201
    meeting_id = post.json()["meeting_id"]

    response = client.post(f"/api/v1/meetings/{meeting_id}/extract")
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# 7. Service layer unit tests (no HTTP)
# ---------------------------------------------------------------------------

def test_extraction_service_raises_meeting_not_found() -> None:
    """ExtractionService.extract_meeting must raise MeetingNotFoundError for
    an unknown meeting_id."""
    from app.models.extraction import ExtractionResult

    placeholder = ExtractionResult(
        meeting_id="placeholder",
        extracted_at=datetime.now(tz=timezone.utc),
    )
    provider = FakeExtractionProvider(result=placeholder)
    meeting_repo = InMemoryMeetingRepository()
    extraction_repo = InMemoryExtractionRepository()

    service = ExtractionService(
        meeting_repository=meeting_repo,
        extraction_repository=extraction_repo,
        provider=provider,
    )

    with pytest.raises(MeetingNotFoundError):
        service.extract_meeting("nonexistent-id")


def test_extraction_service_persists_result() -> None:
    """After extract_meeting, the result must be retrievable via get_extraction_result."""
    from app.schemas.meeting import MeetingIngestRequest
    from app.services.meeting_service import MeetingService

    meeting_repo = InMemoryMeetingRepository()
    extraction_repo = InMemoryExtractionRepository()

    # Ingest a meeting through the service layer
    meeting_service = MeetingService(repository=meeting_repo)
    request = MeetingIngestRequest(
        title="Sync",
        transcript="Alice said we should postpone the launch.",
        meeting_date="2026-08-23T10:00:00Z",
    )
    meeting = meeting_service.ingest_meeting(request)
    meeting_id = meeting.meeting_id

    from app.models.extraction import ExtractionResult

    class DeferredFake(FakeExtractionProvider):
        def extract(self, transcript: str, meeting_id: str):  # noqa: ANN
            return _make_rich_result(meeting_id)

    placeholder = ExtractionResult(
        meeting_id="placeholder",
        extracted_at=datetime.now(tz=timezone.utc),
    )

    provider = DeferredFake(result=placeholder)
    service = ExtractionService(
        meeting_repository=meeting_repo,
        extraction_repository=extraction_repo,
        provider=provider,
    )

    result = service.extract_meeting(meeting_id)
    assert result.meeting_id == meeting_id

    stored = service.get_extraction_result(meeting_id)
    assert stored is not None
    assert stored.meeting_id == meeting_id
    assert len(stored.issues) == 1
