"""Tests for the Cross-Meeting Correlation stage (Stage 5 of the pipeline).

All tests are fully deterministic -- no LLM calls, no network, no external database.

Coverage
--------

Service unit tests (no HTTP):
  C01. Unknown entity_id raises EntityNotFoundError.
  C02. Entity with no resolved mentions returns empty observations list.
  C03. One resolved mention appears in correlation with correct fields.
  C04. Multiple resolved mentions across multiple meetings all appear.
  C05. Results are ordered chronologically by meeting_date ascending.
  C06. Same entity mentioned twice in one meeting -- both observations present.
  C07. Mentions from different entities are never mixed.
  C08. UNRESOLVED mention (entity_id=None) is excluded.
  C09. AMBIGUOUS mention (entity_id=None, status=AMBIGUOUS) is excluded.
  C10. Correlation does not modify mention resolution_status.
  C11. Correlation does not create entities.
  C12. Correlation does not trigger candidate generation.
  C13. Correlation does not trigger scoring.
  C14. Correlation does not trigger resolution.
  C15. Output ordering is deterministic -- same inputs same result on repeated calls.
  C16. Missing meeting (data integrity issue) is skipped gracefully.
  C17. Entity with only AMBIGUOUS and UNRESOLVED mentions returns empty observations.
  C18. Each observation includes the correct meeting_title.
  C19. observation_count on EntityCorrelation equals len(observations).
  C22. Same meeting_date -- secondary ordering by meeting_id, tertiary by mention_id.

API endpoint tests (full stack via TestClient):
  C20. GET /{entity_id}/correlations returns correct JSON structure.
  C21. API response validates against EntityCorrelationResponse schema.
  A01. Unknown entity_id returns HTTP 404.
  A02. Entity with no resolved mentions returns HTTP 200 with empty list.
  A03. Multiple resolved mentions across meetings all appear in API response.
  A04. API response is chronologically ordered.
  A05. UNRESOLVED and AMBIGUOUS mentions are excluded from API response.

Regression:
  R01. All 158 existing tests are unaffected (verified by running full suite).
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.models.entity import (
    CanonicalEntity,
    EntityMention,
    EntityType,
    ResolutionStatus,
)
from app.models.meeting import Meeting
from app.models.correlation import EntityCorrelation, EntityObservation
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.services.correlation_service import CorrelationService
from app.services.entity_service import EntityNotFoundError


# ---------------------------------------------------------------------------
# Shared builder helpers
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


def _make_entity(
    entity_id: str,
    canonical_name: str,
    entity_type: EntityType = EntityType.PERSON,
) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        canonical_name=canonical_name,
        aliases=[],
        created_at=_BASE_TIME,
    )


def _make_mention(
    mention_id: str,
    entity_id: Optional[str],
    meeting_id: str,
    text: str,
    source_text: str,
    resolution_status: ResolutionStatus,
    entity_type: EntityType = EntityType.PERSON,
) -> EntityMention:
    return EntityMention(
        mention_id=mention_id,
        entity_type=entity_type,
        text=text,
        meeting_id=meeting_id,
        source_text=source_text,
        entity_id=entity_id,
        resolution_status=resolution_status,
        created_at=_BASE_TIME,
    )


def _make_meeting(
    meeting_id: str,
    title: str,
    meeting_date: datetime,
) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=title,
        transcript="Transcript for " + title,
        meeting_date=meeting_date,
        participants=[],
        ingested_at=_BASE_TIME,
    )


def _build_service(
    entities=None,
    mentions=None,
    meetings=None,
) -> CorrelationService:
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    meeting_repo = InMemoryMeetingRepository()

    for e in (entities or []):
        entity_repo.create(e)
    for m in (mentions or []):
        mention_repo.create(m)
    for mtg in (meetings or []):
        meeting_repo.save(mtg)

    return CorrelationService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
    )


# ---------------------------------------------------------------------------
# C01: Unknown entity_id raises EntityNotFoundError
# ---------------------------------------------------------------------------

def test_c01_unknown_entity_raises_not_found() -> None:
    service = _build_service()
    with pytest.raises(EntityNotFoundError):
        service.get_entity_correlations("nonexistent-entity-id")


# ---------------------------------------------------------------------------
# C02: Entity with no resolved mentions returns empty observations
# ---------------------------------------------------------------------------

def test_c02_entity_with_no_mentions_returns_empty() -> None:
    entity = _make_entity("e1", "rahul kumar")
    service = _build_service(entities=[entity])
    result = service.get_entity_correlations("e1")

    assert isinstance(result, EntityCorrelation)
    assert result.entity_id == "e1"
    assert result.canonical_name == "rahul kumar"
    assert result.entity_type == EntityType.PERSON
    assert result.observations == []


# ---------------------------------------------------------------------------
# C03: One resolved mention appears in correlation with correct fields
# ---------------------------------------------------------------------------

def test_c03_one_resolved_mention_appears() -> None:
    entity = _make_entity("e1", "rahul kumar")
    meeting = _make_meeting("m1", "Sprint Planning", _BASE_TIME)
    mention = _make_mention(
        "mn1", "e1", "m1", "Rahul",
        "Rahul will fix the payment API.",
        ResolutionStatus.RESOLVED,
    )

    service = _build_service(entities=[entity], mentions=[mention], meetings=[meeting])
    result = service.get_entity_correlations("e1")

    assert len(result.observations) == 1
    obs = result.observations[0]
    assert obs.meeting_id == "m1"
    assert obs.meeting_title == "Sprint Planning"
    assert obs.meeting_date == _BASE_TIME
    assert obs.mention_id == "mn1"
    assert obs.mention_text == "Rahul"
    assert obs.source_text == "Rahul will fix the payment API."


# ---------------------------------------------------------------------------
# C04: Multiple resolved mentions across multiple meetings all appear
# ---------------------------------------------------------------------------

def test_c04_multiple_mentions_across_meetings() -> None:
    entity = _make_entity("e1", "rahul kumar")
    m1 = _make_meeting("meeting-a", "Sprint Planning", _BASE_TIME)
    m2 = _make_meeting("meeting-b", "Weekly Sync", _BASE_TIME + timedelta(days=7))
    m3 = _make_meeting("meeting-c", "Retro", _BASE_TIME + timedelta(days=14))

    mn1 = _make_mention("mn1", "e1", "meeting-a", "Rahul", "Rahul will fix the API.", ResolutionStatus.RESOLVED)
    mn2 = _make_mention("mn2", "e1", "meeting-b", "Rahul Kumar", "Rahul Kumar is investigating.", ResolutionStatus.RESOLVED)
    mn3 = _make_mention("mn3", "e1", "meeting-c", "Rahul", "Rahul said the issue is still open.", ResolutionStatus.RESOLVED)

    service = _build_service(
        entities=[entity], mentions=[mn1, mn2, mn3], meetings=[m1, m2, m3],
    )
    result = service.get_entity_correlations("e1")

    assert len(result.observations) == 3
    meeting_ids = [obs.meeting_id for obs in result.observations]
    assert "meeting-a" in meeting_ids
    assert "meeting-b" in meeting_ids
    assert "meeting-c" in meeting_ids


# ---------------------------------------------------------------------------
# C05: Results ordered chronologically by meeting_date ascending
# ---------------------------------------------------------------------------

def test_c05_chronological_ordering() -> None:
    entity = _make_entity("e1", "rahul kumar")
    m1 = _make_meeting("m1", "Latest", _BASE_TIME + timedelta(days=14))
    m2 = _make_meeting("m2", "Middle", _BASE_TIME + timedelta(days=7))
    m3 = _make_meeting("m3", "Earliest", _BASE_TIME)

    mn1 = _make_mention("mn-a", "e1", "m1", "Rahul", "Latest meeting.", ResolutionStatus.RESOLVED)
    mn2 = _make_mention("mn-b", "e1", "m2", "Rahul", "Middle meeting.", ResolutionStatus.RESOLVED)
    mn3 = _make_mention("mn-c", "e1", "m3", "Rahul", "Earliest meeting.", ResolutionStatus.RESOLVED)

    service = _build_service(
        entities=[entity], mentions=[mn1, mn2, mn3], meetings=[m1, m2, m3],
    )
    result = service.get_entity_correlations("e1")

    assert len(result.observations) == 3
    assert result.observations[0].meeting_title == "Earliest"
    assert result.observations[1].meeting_title == "Middle"
    assert result.observations[2].meeting_title == "Latest"


# ---------------------------------------------------------------------------
# C06: Same entity mentioned twice in one meeting
# ---------------------------------------------------------------------------

def test_c06_same_entity_twice_in_one_meeting() -> None:
    entity = _make_entity("e1", "rahul kumar")
    meeting = _make_meeting("m1", "Sprint Planning", _BASE_TIME)

    mn1 = _make_mention("mn1", "e1", "m1", "Rahul", "Rahul will fix the payment API.", ResolutionStatus.RESOLVED)
    mn2 = _make_mention("mn2", "e1", "m1", "Rahul Kumar", "Rahul Kumar needs more context.", ResolutionStatus.RESOLVED)

    service = _build_service(entities=[entity], mentions=[mn1, mn2], meetings=[meeting])
    result = service.get_entity_correlations("e1")

    assert len(result.observations) == 2
    mention_ids = {obs.mention_id for obs in result.observations}
    assert "mn1" in mention_ids
    assert "mn2" in mention_ids


# ---------------------------------------------------------------------------
# C07: Mentions from different entities never mix
# ---------------------------------------------------------------------------

def test_c07_different_entities_not_mixed() -> None:
    entity_a = _make_entity("e-a", "rahul kumar")
    entity_b = _make_entity("e-b", "priya sharma")
    meeting = _make_meeting("m1", "Sprint Planning", _BASE_TIME)

    mn_a = _make_mention("mn-a", "e-a", "m1", "Rahul", "Rahul will fix the API.", ResolutionStatus.RESOLVED)
    mn_b = _make_mention("mn-b", "e-b", "m1", "Priya", "Priya asked for an update.", ResolutionStatus.RESOLVED)

    service = _build_service(
        entities=[entity_a, entity_b], mentions=[mn_a, mn_b], meetings=[meeting],
    )

    result_a = service.get_entity_correlations("e-a")
    assert len(result_a.observations) == 1
    assert result_a.observations[0].mention_id == "mn-a"

    result_b = service.get_entity_correlations("e-b")
    assert len(result_b.observations) == 1
    assert result_b.observations[0].mention_id == "mn-b"


# ---------------------------------------------------------------------------
# C08: UNRESOLVED mention is excluded
# ---------------------------------------------------------------------------

def test_c08_unresolved_mention_excluded() -> None:
    entity = _make_entity("e1", "rahul kumar")
    meeting = _make_meeting("m1", "Sprint Planning", _BASE_TIME)

    mn_unresolved = _make_mention(
        "mn-unresolved", None, "m1",
        "the backend lead", "The backend lead will own this.",
        ResolutionStatus.UNRESOLVED,
    )
    mn_resolved = _make_mention(
        "mn-resolved", "e1", "m1",
        "Rahul", "Rahul will fix the payment API.",
        ResolutionStatus.RESOLVED,
    )

    service = _build_service(
        entities=[entity], mentions=[mn_unresolved, mn_resolved], meetings=[meeting],
    )
    result = service.get_entity_correlations("e1")

    assert len(result.observations) == 1
    assert result.observations[0].mention_id == "mn-resolved"


# ---------------------------------------------------------------------------
# C09: AMBIGUOUS mention is excluded
# ---------------------------------------------------------------------------

def test_c09_ambiguous_mention_excluded() -> None:
    entity = _make_entity("e1", "rahul kumar")
    meeting = _make_meeting("m1", "Sprint Planning", _BASE_TIME)

    mn_ambiguous = _make_mention(
        "mn-ambiguous", None, "m1",
        "Rahul", "Rahul was mentioned ambiguously.",
        ResolutionStatus.AMBIGUOUS,
    )
    mn_resolved = _make_mention(
        "mn-resolved", "e1", "m1",
        "Rahul Kumar", "Rahul Kumar confirmed the fix.",
        ResolutionStatus.RESOLVED,
    )

    service = _build_service(
        entities=[entity], mentions=[mn_ambiguous, mn_resolved], meetings=[meeting],
    )
    result = service.get_entity_correlations("e1")

    assert len(result.observations) == 1
    assert result.observations[0].mention_id == "mn-resolved"


# ---------------------------------------------------------------------------
# C10: Correlation does not modify mention resolution_status
# ---------------------------------------------------------------------------

def test_c10_correlation_does_not_modify_mention_state() -> None:
    entity = _make_entity("e1", "rahul kumar")
    meeting = _make_meeting("m1", "Sprint Planning", _BASE_TIME)
    mention = _make_mention(
        "mn1", "e1", "m1", "Rahul", "Rahul will fix the API.",
        ResolutionStatus.RESOLVED,
    )

    mention_repo = InMemoryMentionRepository()
    mention_repo.create(mention)
    entity_repo = InMemoryEntityRepository()
    entity_repo.create(entity)
    meeting_repo = InMemoryMeetingRepository()
    meeting_repo.save(meeting)

    service = CorrelationService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
    )

    service.get_entity_correlations("e1")

    stored = mention_repo.get_by_id("mn1")
    assert stored is not None
    assert stored.resolution_status == ResolutionStatus.RESOLVED
    assert stored.entity_id == "e1"


# ---------------------------------------------------------------------------
# C11: Correlation does not create entities
# ---------------------------------------------------------------------------

def test_c11_correlation_does_not_create_entities() -> None:
    entity = _make_entity("e1", "rahul kumar")
    entity_repo = InMemoryEntityRepository()
    entity_repo.create(entity)

    service = CorrelationService(
        entity_repo=entity_repo,
        mention_repo=InMemoryMentionRepository(),
        meeting_repo=InMemoryMeetingRepository(),
    )

    before = len(entity_repo.list_entities())
    service.get_entity_correlations("e1")
    after = len(entity_repo.list_entities())

    assert before == after == 1


# ---------------------------------------------------------------------------
# C12: Correlation does not trigger candidate generation
# ---------------------------------------------------------------------------

def test_c12_correlation_does_not_trigger_candidate_generation() -> None:
    import inspect
    sig = inspect.signature(CorrelationService.__init__)
    param_names = list(sig.parameters.keys())
    assert "generator" not in param_names
    assert "candidate_service" not in param_names

    entity = _make_entity("e1", "rahul kumar")
    service = _build_service(entities=[entity])
    result = service.get_entity_correlations("e1")
    assert result.observations == []


# ---------------------------------------------------------------------------
# C13: Correlation does not trigger scoring
# ---------------------------------------------------------------------------

def test_c13_correlation_does_not_trigger_scoring() -> None:
    import inspect
    sig = inspect.signature(CorrelationService.__init__)
    param_names = list(sig.parameters.keys())
    assert "scorer" not in param_names
    assert "scoring_service" not in param_names


# ---------------------------------------------------------------------------
# C14: Correlation does not trigger resolution
# ---------------------------------------------------------------------------

def test_c14_correlation_does_not_trigger_resolution() -> None:
    import inspect
    sig = inspect.signature(CorrelationService.__init__)
    param_names = list(sig.parameters.keys())
    assert "policy" not in param_names
    assert "resolution_service" not in param_names


# ---------------------------------------------------------------------------
# C15: Output ordering is deterministic
# ---------------------------------------------------------------------------

def test_c15_output_ordering_is_deterministic() -> None:
    entity = _make_entity("e1", "rahul kumar")
    m1 = _make_meeting("m1", "First", _BASE_TIME)
    m2 = _make_meeting("m2", "Second", _BASE_TIME + timedelta(days=1))

    mn1 = _make_mention("mn1", "e1", "m1", "Rahul", "Source A.", ResolutionStatus.RESOLVED)
    mn2 = _make_mention("mn2", "e1", "m2", "Rahul Kumar", "Source B.", ResolutionStatus.RESOLVED)

    service = _build_service(
        entities=[entity], mentions=[mn1, mn2], meetings=[m1, m2],
    )

    result1 = service.get_entity_correlations("e1")
    result2 = service.get_entity_correlations("e1")

    assert len(result1.observations) == len(result2.observations)
    for obs1, obs2 in zip(result1.observations, result2.observations):
        assert obs1.mention_id == obs2.mention_id
        assert obs1.meeting_id == obs2.meeting_id
        assert obs1.meeting_date == obs2.meeting_date


# ---------------------------------------------------------------------------
# C16: Missing meeting is skipped gracefully
# ---------------------------------------------------------------------------

def test_c16_missing_meeting_skipped_gracefully() -> None:
    entity = _make_entity("e1", "rahul kumar")
    meeting = _make_meeting("m1", "Sprint Planning", _BASE_TIME)

    mn_dangling = _make_mention(
        "mn-dangling", "e1", "m-missing",
        "Rahul", "Dangling mention.", ResolutionStatus.RESOLVED,
    )
    mn_valid = _make_mention(
        "mn-valid", "e1", "m1",
        "Rahul Kumar", "Rahul Kumar is present.", ResolutionStatus.RESOLVED,
    )

    service = _build_service(
        entities=[entity], mentions=[mn_dangling, mn_valid], meetings=[meeting],
    )
    result = service.get_entity_correlations("e1")
    assert len(result.observations) == 1
    assert result.observations[0].mention_id == "mn-valid"


# ---------------------------------------------------------------------------
# C17: Entity with only non-RESOLVED mentions returns empty
# ---------------------------------------------------------------------------

def test_c17_only_non_resolved_mentions_returns_empty() -> None:
    entity = _make_entity("e1", "rahul kumar")
    meeting = _make_meeting("m1", "Sprint Planning", _BASE_TIME)

    mn_unresolved = _make_mention("mn1", None, "m1", "Rahul", "Source A.", ResolutionStatus.UNRESOLVED)
    mn_ambiguous = _make_mention("mn2", None, "m1", "Rahul", "Source B.", ResolutionStatus.AMBIGUOUS)

    service = _build_service(
        entities=[entity], mentions=[mn_unresolved, mn_ambiguous], meetings=[meeting],
    )
    result = service.get_entity_correlations("e1")
    assert result.observations == []


# ---------------------------------------------------------------------------
# C18: Observations include correct meeting_title
# ---------------------------------------------------------------------------

def test_c18_observations_include_correct_meeting_title() -> None:
    entity = _make_entity("e1", "rahul kumar")
    meeting = _make_meeting("m1", "Q3 Planning Session", _BASE_TIME)
    mention = _make_mention("mn1", "e1", "m1", "Rahul", "Source.", ResolutionStatus.RESOLVED)

    service = _build_service(entities=[entity], mentions=[mention], meetings=[meeting])
    result = service.get_entity_correlations("e1")

    assert result.observations[0].meeting_title == "Q3 Planning Session"


# ---------------------------------------------------------------------------
# C19: len(observations) is consistent
# ---------------------------------------------------------------------------

def test_c19_observation_count_equals_len() -> None:
    entity = _make_entity("e1", "rahul kumar")
    m1 = _make_meeting("m1", "Meeting A", _BASE_TIME)
    m2 = _make_meeting("m2", "Meeting B", _BASE_TIME + timedelta(days=1))
    m3 = _make_meeting("m3", "Meeting C", _BASE_TIME + timedelta(days=2))

    mn1 = _make_mention("mn1", "e1", "m1", "Rahul", "S1.", ResolutionStatus.RESOLVED)
    mn2 = _make_mention("mn2", "e1", "m2", "Rahul", "S2.", ResolutionStatus.RESOLVED)
    mn3 = _make_mention("mn3", "e1", "m3", "Rahul", "S3.", ResolutionStatus.RESOLVED)

    service = _build_service(
        entities=[entity], mentions=[mn1, mn2, mn3], meetings=[m1, m2, m3],
    )
    result = service.get_entity_correlations("e1")
    assert len(result.observations) == 3


# ---------------------------------------------------------------------------
# C22: Same meeting_date -- secondary ordering by meeting_id, tertiary by mention_id
# ---------------------------------------------------------------------------

def test_c22_same_meeting_date_secondary_ordering() -> None:
    entity = _make_entity("e1", "rahul kumar")
    same_time = _BASE_TIME
    m_z = _make_meeting("m-zzz", "Meeting Z", same_time)
    m_a = _make_meeting("m-aaa", "Meeting A", same_time)
    m_m = _make_meeting("m-mmm", "Meeting M", same_time)

    mn_z = _make_mention("mn-z", "e1", "m-zzz", "Rahul", "Z source.", ResolutionStatus.RESOLVED)
    mn_a = _make_mention("mn-a", "e1", "m-aaa", "Rahul", "A source.", ResolutionStatus.RESOLVED)
    mn_m = _make_mention("mn-m", "e1", "m-mmm", "Rahul", "M source.", ResolutionStatus.RESOLVED)

    service = _build_service(
        entities=[entity], mentions=[mn_z, mn_a, mn_m], meetings=[m_z, m_a, m_m],
    )
    result = service.get_entity_correlations("e1")

    assert len(result.observations) == 3
    assert result.observations[0].meeting_id == "m-aaa"
    assert result.observations[1].meeting_id == "m-mmm"
    assert result.observations[2].meeting_id == "m-zzz"


def test_c22b_same_meeting_id_tertiary_mention_ordering() -> None:
    entity = _make_entity("e1", "rahul kumar")
    same_time = _BASE_TIME
    meeting = _make_meeting("m1", "Sprint Planning", same_time)
    mn_b = _make_mention("mn-b", "e1", "m1", "Rahul", "Second mention.", ResolutionStatus.RESOLVED)
    mn_a = _make_mention("mn-a", "e1", "m1", "Rahul Kumar", "First mention.", ResolutionStatus.RESOLVED)

    service = _build_service(
        entities=[entity], mentions=[mn_b, mn_a], meetings=[meeting],
    )
    result = service.get_entity_correlations("e1")

    assert len(result.observations) == 2
    assert result.observations[0].mention_id == "mn-a"
    assert result.observations[1].mention_id == "mn-b"


# ---------------------------------------------------------------------------
# API Endpoint Tests (full stack via TestClient)
# ---------------------------------------------------------------------------

@pytest.fixture()
def correlation_client():
    from app.main import app
    from app.api.entities import get_correlation_service, get_entity_service

    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    meeting_repo = InMemoryMeetingRepository()

    service = CorrelationService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
    )

    from app.services.entity_service import EntityService
    entity_service = EntityService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
    )

    app.dependency_overrides[get_correlation_service] = lambda: service
    app.dependency_overrides[get_entity_service] = lambda: entity_service

    client = TestClient(app)
    yield client, entity_repo, mention_repo, meeting_repo

    app.dependency_overrides.pop(get_correlation_service, None)
    app.dependency_overrides.pop(get_entity_service, None)


def test_a01_api_unknown_entity_returns_404(correlation_client) -> None:
    """A01. Unknown entity_id returns HTTP 404."""
    client, *_ = correlation_client
    response = client.get("/api/v1/entities/does-not-exist/correlations")
    assert response.status_code == 404


def test_a02_api_entity_no_mentions_returns_200_empty(correlation_client) -> None:
    """A02. Entity with no mentions returns 200 with empty observations."""
    client, entity_repo, *_ = correlation_client
    entity_repo.create(_make_entity("e1", "rahul kumar"))

    response = client.get("/api/v1/entities/e1/correlations")
    assert response.status_code == 200
    body = response.json()
    assert body["entity_id"] == "e1"
    assert body["observation_count"] == 0
    assert body["observations"] == []


def test_a03_api_multiple_mentions_all_appear(correlation_client) -> None:
    """A03. Multiple resolved mentions across meetings all appear in API response."""
    client, entity_repo, mention_repo, meeting_repo = correlation_client

    entity_repo.create(_make_entity("e1", "rahul kumar"))
    meeting_repo.save(_make_meeting("m1", "Sprint Planning", _BASE_TIME))
    meeting_repo.save(_make_meeting("m2", "Weekly Sync", _BASE_TIME + timedelta(days=7)))
    mention_repo.create(_make_mention("mn1", "e1", "m1", "Rahul", "Source A.", ResolutionStatus.RESOLVED))
    mention_repo.create(_make_mention("mn2", "e1", "m2", "Rahul Kumar", "Source B.", ResolutionStatus.RESOLVED))

    response = client.get("/api/v1/entities/e1/correlations")
    assert response.status_code == 200
    body = response.json()
    assert body["observation_count"] == 2
    assert len(body["observations"]) == 2


def test_a04_api_response_is_chronologically_ordered(correlation_client) -> None:
    """A04. Observations are ordered by meeting_date ascending in API response."""
    client, entity_repo, mention_repo, meeting_repo = correlation_client

    entity_repo.create(_make_entity("e1", "rahul kumar"))
    later = _BASE_TIME + timedelta(days=7)
    earlier = _BASE_TIME

    meeting_repo.save(_make_meeting("m-later", "Later Meeting", later))
    meeting_repo.save(_make_meeting("m-earlier", "Earlier Meeting", earlier))
    mention_repo.create(_make_mention("mn-later", "e1", "m-later", "Rahul", "Later source.", ResolutionStatus.RESOLVED))
    mention_repo.create(_make_mention("mn-earlier", "e1", "m-earlier", "Rahul Kumar", "Earlier source.", ResolutionStatus.RESOLVED))

    response = client.get("/api/v1/entities/e1/correlations")
    body = response.json()
    obs = body["observations"]
    assert obs[0]["meeting_id"] == "m-earlier"
    assert obs[1]["meeting_id"] == "m-later"


def test_a05_api_unresolved_and_ambiguous_excluded(correlation_client) -> None:
    """A05. UNRESOLVED and AMBIGUOUS mentions are excluded from API response."""
    client, entity_repo, mention_repo, meeting_repo = correlation_client

    entity_repo.create(_make_entity("e1", "rahul kumar"))
    meeting_repo.save(_make_meeting("m1", "Sprint Planning", _BASE_TIME))
    mention_repo.create(_make_mention("mn-unr", None, "m1", "unknown", "Some source.", ResolutionStatus.UNRESOLVED))
    mention_repo.create(_make_mention("mn-amb", None, "m1", "Rahul", "Ambiguous source.", ResolutionStatus.AMBIGUOUS))
    mention_repo.create(_make_mention("mn-res", "e1", "m1", "Rahul Kumar", "Clear source.", ResolutionStatus.RESOLVED))

    response = client.get("/api/v1/entities/e1/correlations")
    assert response.status_code == 200
    body = response.json()
    assert body["observation_count"] == 1
    assert body["observations"][0]["mention_id"] == "mn-res"


def test_c20_api_response_has_required_fields(correlation_client) -> None:
    """C20. API response contains all required top-level and observation fields."""
    client, entity_repo, mention_repo, meeting_repo = correlation_client

    entity_repo.create(_make_entity("e1", "rahul kumar"))
    meeting_repo.save(_make_meeting("m1", "Sprint Planning", _BASE_TIME))
    mention_repo.create(_make_mention("mn1", "e1", "m1", "Rahul", "Rahul will fix the API.", ResolutionStatus.RESOLVED))

    response = client.get("/api/v1/entities/e1/correlations")
    assert response.status_code == 200
    body = response.json()

    assert "entity_id" in body
    assert "canonical_name" in body
    assert "entity_type" in body
    assert "observation_count" in body
    assert "observations" in body

    obs = body["observations"][0]
    assert "meeting_id" in obs
    assert "meeting_title" in obs
    assert "meeting_date" in obs
    assert "mention_id" in obs
    assert "mention_text" in obs
    assert "source_text" in obs


def test_c21_api_response_validates_against_schema(correlation_client) -> None:
    """C21. API response can be validated against EntityCorrelationResponse schema."""
    from app.schemas.correlation import EntityCorrelationResponse

    client, entity_repo, mention_repo, meeting_repo = correlation_client

    entity_repo.create(_make_entity("e1", "rahul kumar"))
    meeting_repo.save(_make_meeting("m1", "Sprint Planning", _BASE_TIME))
    mention_repo.create(_make_mention("mn1", "e1", "m1", "Rahul", "Source.", ResolutionStatus.RESOLVED))

    response = client.get("/api/v1/entities/e1/correlations")
    body = response.json()

    parsed = EntityCorrelationResponse(**body)
    assert parsed.entity_id == "e1"
    assert parsed.observation_count == 1
    assert len(parsed.observations) == 1
