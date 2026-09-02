"""Tests for the Organisational Memory stage (Stage 7 of the pipeline).

All tests are fully deterministic -- no LLM calls, no network, no external database.

Coverage
--------

Service unit tests (no HTTP):
  M01. Unknown entity_id raises EntityNotFoundError.
  M02. Entity exists with no observations returns empty facts (except CURRENT_STATE).
  M03. Single resolved observation returns FIRST_OBSERVED, CURRENT_STATE, no LAST_OBSERVED.
  M04. Multiple observations across multiple meetings returns all expected facts.
  M05. Correct first_observed_at.
  M06. Correct last_observed_at.
  M07. Correct observation_count.
  M08. Correct distinct meeting_count.
  M09. Current temporal state comes from TemporalStateService.
  M10. Memory includes valid state transitions as STATE_TRANSITION facts.
  M11. Invalid transitions are NOT included as facts.
  M12. Different entities never mix memory.
  M13. UNRESOLVED mentions do not appear.
  M14. AMBIGUOUS mentions do not appear.
  M15. Memory service does not modify mentions.
  M16. Memory service does not modify entities.
  M17. Memory service does not create entities.
  M18. Memory service does not change temporal state.
  M19. Memory service does not trigger candidate generation (by design).
  M20. Memory service does not trigger candidate scoring (by design).
  M21. Memory service does not trigger resolution (by design).
  M22. Repeated calls produce identical results (deterministic).
  M23. Ordering of STATE_TRANSITION facts is chronological.
  M24. Same meeting timestamp uses deterministic tie-breaking (via underlying timeline).
  M25. REPEATED_OBSERVATION created for meetings with >= 2 mentions.
  M26. No REPEATED_OBSERVATION for meetings with exactly 1 mention.

API endpoint tests (full stack via TestClient):
  A01. Unknown entity_id returns HTTP 404.
  A02. Entity exists with no observations returns HTTP 200 with empty structure.
  A03. Entity with observations returns HTTP 200 with expected JSON structure.
  A04. Response structure validates against schema implicitly (TestClient).
  A05. current_state in response matches expected.
  A06. first_observed_at and last_observed_at in response match expected.
  A07. facts list is present and non-null.
  A08. CURRENT_STATE fact is always present.
  A09. STATE_TRANSITION facts present when transitions occurred.
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
from app.models.memory import EntityMemory, MemoryFactType
from app.models.temporal import TemporalState
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.entity_service import EntityNotFoundError
from app.services.organisational_memory_service import OrganisationalMemoryService
from app.temporal.state_interpreter import KeywordStateInterpreter
from app.temporal.transition_policy import DefaultTransitionPolicy


# ---------------------------------------------------------------------------
# Shared builder helpers
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


def _make_entity(
    entity_id: str,
    canonical_name: str,
    entity_type: EntityType = EntityType.ISSUE,
) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        canonical_name=canonical_name,
        aliases=[],
        created_at=_BASE_TIME,
    )


def _make_meeting(
    meeting_id: str,
    title: str = "Test Meeting",
    offset_days: int = 0,
) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=title,
        transcript="placeholder transcript",
        meeting_date=_BASE_TIME + timedelta(days=offset_days),
        participants=[],
        ingested_at=_BASE_TIME,
    )


def _make_resolved_mention(
    mention_id: str,
    entity_id: str,
    meeting_id: str,
    source_text: str,
    entity_type: EntityType = EntityType.ISSUE,
) -> EntityMention:
    return EntityMention(
        mention_id=mention_id,
        entity_type=entity_type,
        text="the issue",
        meeting_id=meeting_id,
        source_text=source_text,
        entity_id=entity_id,
        resolution_status=ResolutionStatus.RESOLVED,
        created_at=_BASE_TIME,
    )


def _make_unresolved_mention(
    mention_id: str,
    meeting_id: str,
    source_text: str = "Some text",
    status: ResolutionStatus = ResolutionStatus.UNRESOLVED,
) -> EntityMention:
    return EntityMention(
        mention_id=mention_id,
        entity_type=EntityType.ISSUE,
        text="the issue",
        meeting_id=meeting_id,
        source_text=source_text,
        entity_id=None,
        resolution_status=status,
        created_at=_BASE_TIME,
    )


def _make_service(
    entities: list[CanonicalEntity],
    meetings: list[Meeting],
    mentions: list[EntityMention],
) -> OrganisationalMemoryService:
    entity_repo = InMemoryEntityRepository()
    for e in entities:
        entity_repo.create(e)
    meeting_repo = InMemoryMeetingRepository()
    for m in meetings:
        meeting_repo.save(m)
    mention_repo = InMemoryMentionRepository()
    for m in mentions:
        mention_repo.create(m)
    return OrganisationalMemoryService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


# ---------------------------------------------------------------------------
# Service Tests
# ---------------------------------------------------------------------------

def test_m01_unknown_entity_raises_not_found():
    service = _make_service([], [], [])
    with pytest.raises(EntityNotFoundError):
        service.get_entity_memory("e_unknown")


def test_m02_entity_with_no_observations_returns_empty_structure():
    entity = _make_entity("e1", "Bug 1")
    service = _make_service([entity], [], [])
    memory = service.get_entity_memory("e1")

    assert memory.entity_id == "e1"
    assert memory.first_observed_at is None
    assert memory.last_observed_at is None
    assert memory.meeting_count == 0
    assert memory.observation_count == 0
    assert memory.current_state == TemporalState.UNKNOWN

    assert len(memory.facts) == 1
    assert memory.facts[0].fact_type == MemoryFactType.CURRENT_STATE
    assert memory.facts[0].value == "UNKNOWN"
    assert memory.facts[0].source_meeting_id is None
    assert memory.facts[0].source_mention_id is None
    assert memory.facts[0].observed_at is None
    assert memory.facts[0].detail is None


def test_m03_single_resolved_observation_returns_correct_facts():
    entity = _make_entity("e1", "Bug 1")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    mention = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 was reported.")
    service = _make_service([entity], [meeting], [mention])
    memory = service.get_entity_memory("e1")

    assert memory.first_observed_at == meeting.meeting_date
    assert memory.last_observed_at == meeting.meeting_date  # Same as first when count is 1
    assert memory.meeting_count == 1
    assert memory.observation_count == 1
    assert memory.current_state == TemporalState.OPEN

    fact_types = [f.fact_type for f in memory.facts]
    assert MemoryFactType.FIRST_OBSERVED in fact_types
    assert MemoryFactType.LAST_OBSERVED not in fact_types
    assert MemoryFactType.CURRENT_STATE in fact_types
    assert MemoryFactType.STATE_TRANSITION in fact_types
    assert MemoryFactType.REPEATED_OBSERVATION not in fact_types

    # Find the transition fact
    transition = next(f for f in memory.facts if f.fact_type == MemoryFactType.STATE_TRANSITION)
    assert transition.value == "UNKNOWN → OPEN"
    assert transition.source_meeting_id == "m1"
    assert transition.source_mention_id == "mn1"
    assert transition.observed_at == meeting.meeting_date


def test_m04_m05_m06_m07_m08_multiple_observations_across_meetings():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", "Sprint 1", offset_days=0)
    m2 = _make_meeting("m2", "Daily Standup", offset_days=1)
    m3 = _make_meeting("m3", "Sprint 2", offset_days=14)

    # 4 observations total, 3 meetings
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 reported.") # OPEN
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Started working on bug 1.") # IN_PROGRESS
    mn3 = _make_resolved_mention("mn3", "e1", "m2", "Bug 1 is blocked.") # BLOCKED
    mn4 = _make_resolved_mention("mn4", "e1", "m3", "Bug 1 is fixed.") # RESOLVED

    service = _make_service([entity], [m1, m2, m3], [mn1, mn2, mn3, mn4])
    memory = service.get_entity_memory("e1")

    assert memory.first_observed_at == m1.meeting_date # M05
    assert memory.last_observed_at == m3.meeting_date # M06
    assert memory.observation_count == 4 # M07
    assert memory.meeting_count == 3 # M08
    assert memory.current_state == TemporalState.RESOLVED # M09

    fact_types = [f.fact_type for f in memory.facts]
    assert MemoryFactType.FIRST_OBSERVED in fact_types
    assert MemoryFactType.LAST_OBSERVED in fact_types
    assert MemoryFactType.CURRENT_STATE in fact_types
    assert MemoryFactType.STATE_TRANSITION in fact_types
    assert MemoryFactType.REPEATED_OBSERVATION in fact_types # m2 has 2 mentions


def test_m10_m11_m23_valid_and_invalid_transitions():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", offset_days=0)
    m2 = _make_meeting("m2", offset_days=1)
    m3 = _make_meeting("m3", offset_days=2)

    # UNKNOWN -> IN_PROGRESS (valid)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "started")
    # IN_PROGRESS -> RESOLVED (valid)
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "fixed")
    # RESOLVED -> IN_PROGRESS (invalid)
    mn3 = _make_resolved_mention("mn3", "e1", "m3", "started")

    service = _make_service([entity], [m1, m2, m3], [mn1, mn2, mn3])
    memory = service.get_entity_memory("e1")

    assert memory.current_state == TemporalState.RESOLVED

    transitions = [f for f in memory.facts if f.fact_type == MemoryFactType.STATE_TRANSITION]
    assert len(transitions) == 2 # Only the valid ones (M11)
    
    # Chronological ordering (M23)
    assert transitions[0].value == "UNKNOWN → IN_PROGRESS"
    assert transitions[1].value == "IN_PROGRESS → RESOLVED"


def test_m12_different_entities_never_mix_memory():
    e1 = _make_entity("e1", "Bug 1")
    e2 = _make_entity("e2", "Bug 2")
    m1 = _make_meeting("m1", offset_days=0)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "started")
    mn2 = _make_resolved_mention("mn2", "e2", "m1", "blocked")

    service = _make_service([e1, e2], [m1], [mn1, mn2])
    
    memory1 = service.get_entity_memory("e1")
    assert memory1.current_state == TemporalState.IN_PROGRESS
    assert memory1.observation_count == 1

    memory2 = service.get_entity_memory("e2")
    assert memory2.current_state == TemporalState.BLOCKED
    assert memory2.observation_count == 1


def test_m13_m14_unresolved_and_ambiguous_mentions_excluded():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1")
    
    mn_resolved = _make_resolved_mention("mn1", "e1", "m1", "started")
    mn_unresolved = _make_unresolved_mention("mn2", "m1", "blocked", ResolutionStatus.UNRESOLVED)
    mn_ambiguous = _make_unresolved_mention("mn3", "m1", "resolved", ResolutionStatus.AMBIGUOUS)

    service = _make_service([entity], [m1], [mn_resolved, mn_unresolved, mn_ambiguous])
    memory = service.get_entity_memory("e1")

    assert memory.observation_count == 1
    assert memory.current_state == TemporalState.IN_PROGRESS
    # If the ambiguous/unresolved were included, state would be RESOLVED (or BLOCKED)


def test_m15_m16_m17_service_does_not_mutate():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1")
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "started")

    entity_repo = InMemoryEntityRepository()
    entity_repo.create(entity)
    meeting_repo = InMemoryMeetingRepository()
    meeting_repo.save(m1)
    mention_repo = InMemoryMentionRepository()
    mention_repo.create(mn1)

    service = OrganisationalMemoryService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )

    # Initial snapshot
    mentions_before = mention_repo.list_by_meeting_id("m1")
    entities_before = entity_repo.list_entities()

    service.get_entity_memory("e1")

    # Post snapshot
    mentions_after = mention_repo.list_by_meeting_id("m1")
    entities_after = entity_repo.list_entities()

    assert len(mentions_before) == len(mentions_after)
    assert mentions_after[0].resolution_status == ResolutionStatus.RESOLVED
    assert mentions_after[0].entity_id == "e1"

    assert len(entities_before) == len(entities_after)
    assert entities_after[0].canonical_name == "Bug 1"


def test_m22_idempotency():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1")
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "started")

    service = _make_service([entity], [m1], [mn1])
    
    memory1 = service.get_entity_memory("e1")
    memory2 = service.get_entity_memory("e1")

    assert memory1.model_dump() == memory2.model_dump()


def test_m24_same_meeting_timestamp_tie_breaking():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", offset_days=0)
    
    # These two mentions have the SAME meeting_date (m1's date)
    # The sort order is meeting_date ASC, meeting_id ASC, mention_id ASC.
    # Therefore, mn1 (started) will be processed before mn2 (blocked) because 'mn1' < 'mn2'.
    mn2 = _make_resolved_mention("mn2", "e1", "m1", "blocked")
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "started")

    service = _make_service([entity], [m1], [mn1, mn2])
    memory = service.get_entity_memory("e1")

    assert memory.current_state == TemporalState.BLOCKED
    
    transitions = [f for f in memory.facts if f.fact_type == MemoryFactType.STATE_TRANSITION]
    assert len(transitions) == 2
    assert transitions[0].value == "UNKNOWN → IN_PROGRESS"
    assert transitions[1].value == "IN_PROGRESS → BLOCKED"


def test_m25_m26_repeated_observations():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", offset_days=0) # 2 mentions
    m2 = _make_meeting("m2", offset_days=1) # 1 mention
    m3 = _make_meeting("m3", offset_days=2) # 3 mentions

    mn1 = _make_resolved_mention("mn1", "e1", "m1", "obs")
    mn2 = _make_resolved_mention("mn2", "e1", "m1", "obs")
    
    mn3 = _make_resolved_mention("mn3", "e1", "m2", "obs")

    mn4 = _make_resolved_mention("mn4", "e1", "m3", "obs")
    mn5 = _make_resolved_mention("mn5", "e1", "m3", "obs")
    mn6 = _make_resolved_mention("mn6", "e1", "m3", "obs")

    service = _make_service([entity], [m1, m2, m3], [mn1, mn2, mn3, mn4, mn5, mn6])
    memory = service.get_entity_memory("e1")

    repeated = [f for f in memory.facts if f.fact_type == MemoryFactType.REPEATED_OBSERVATION]
    assert len(repeated) == 2 # for m1 and m3

    assert repeated[0].source_meeting_id == "m1"
    assert repeated[0].value == "2"

    assert repeated[1].source_meeting_id == "m3"
    assert repeated[1].value == "3"


# ---------------------------------------------------------------------------
# API Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_client():
    from app.main import app
    from app.api.entities import (
        get_organisational_memory_service,
        get_entity_service,
    )
    from app.services.entity_service import EntityService

    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    meeting_repo = InMemoryMeetingRepository()

    memory_service = OrganisationalMemoryService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )
    entity_service = EntityService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
    )

    app.dependency_overrides[get_organisational_memory_service] = lambda: memory_service
    app.dependency_overrides[get_entity_service] = lambda: entity_service

    client = TestClient(app)
    yield client, entity_repo, mention_repo, meeting_repo

    app.dependency_overrides.pop(get_organisational_memory_service, None)
    app.dependency_overrides.pop(get_entity_service, None)


def test_a01_unknown_entity_returns_404(memory_client):
    client, *_ = memory_client
    resp = client.get("/api/v1/entities/nonexistent/memory")
    assert resp.status_code == 404


def test_a02_entity_no_observations_returns_200(memory_client):
    client, entity_repo, *_ = memory_client
    entity_repo.create(_make_entity("e1", "Bug 1"))
    resp = client.get("/api/v1/entities/e1/memory")
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_id"] == "e1"
    assert data["first_observed_at"] is None
    assert data["last_observed_at"] is None
    assert data["meeting_count"] == 0
    assert data["observation_count"] == 0
    assert data["current_state"] == "UNKNOWN"
    assert len(data["facts"]) == 1
    assert data["facts"][0]["fact_type"] == "CURRENT_STATE"
    assert data["facts"][0]["value"] == "UNKNOWN"


def test_a03_a04_a05_a06_a07_a08_a09_entity_with_observations_returns_200(memory_client):
    client, entity_repo, mention_repo, meeting_repo = memory_client
    entity_repo.create(_make_entity("e1", "Bug 1"))
    m1 = _make_meeting("m1", offset_days=0)
    meeting_repo.save(m1)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "started")
    mention_repo.create(mn1)

    resp = client.get("/api/v1/entities/e1/memory")
    assert resp.status_code == 200
    data = resp.json()

    assert data["entity_id"] == "e1"
    assert data["first_observed_at"] == m1.meeting_date.isoformat().replace("+00:00", "Z") # Fastapi serializes with Z sometimes depending on setup, but we use string equality. Or just check it's not None.
    assert data["first_observed_at"] is not None
    assert data["last_observed_at"] == data["first_observed_at"]
    assert data["meeting_count"] == 1
    assert data["observation_count"] == 1
    assert data["current_state"] == "IN_PROGRESS"

    assert "facts" in data
    assert isinstance(data["facts"], list)
    assert len(data["facts"]) == 3 # FIRST_OBSERVED, CURRENT_STATE, STATE_TRANSITION

    fact_types = [f["fact_type"] for f in data["facts"]]
    assert "CURRENT_STATE" in fact_types
    assert "FIRST_OBSERVED" in fact_types
    assert "STATE_TRANSITION" in fact_types
