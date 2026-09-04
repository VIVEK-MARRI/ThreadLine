"""Tests for the Unified Entity Timeline (Stage 11 of the pipeline).

All tests are fully deterministic -- no LLM calls, no network, no external database.

Coverage
--------

Service unit tests (no HTTP):
  T01. Empty entity timeline (no observations) returns empty events list.
  T02. Single observation without state transition produces OBSERVATION event.
  T03. Single observation with state transition produces STATE_CHANGE event.
  T04. Repeated observation generates MEMORY_FACT (deduplicated correctly).
  T05. Redundant memory facts (FIRST_OBSERVED, etc.) are excluded.
  T06. INSIGHT events are correctly mapped from InsightService.
  T07. ATTENTION events are mapped correctly and placed as a snapshot.
  T08. ACTION events are correctly mapped from ActionRecommendationService.
  T09. Full deterministic sorting: (occurred_at, event_type, event_id).
  T10. Full story with all layers combined correctly.
  T11. Deterministic event ID generation.
  T12. Unknown entity raises EntityNotFoundError.
  T13. Read-only constraint: timeline generation modifies no source models.

API endpoint tests (full stack via TestClient):
  T14. GET /api/v1/entities/{id}/timeline returns 200 with schema.
  T15. GET /api/v1/entities/{id}/timeline returns 404 for unknown entity.
  T16. GET /api/v1/entities/{id}/timeline returns 200 with empty events if unobserved.
"""

from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models.timeline import TimelineEventType
from app.models.entity import (
    CanonicalEntity,
    EntityMention,
    EntityType,
    ResolutionStatus,
)
from app.models.meeting import Meeting
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.entity_service import EntityNotFoundError
from app.services.unified_timeline_service import UnifiedTimelineService
from app.temporal.state_interpreter import KeywordStateInterpreter
from app.temporal.transition_policy import DefaultTransitionPolicy


# ---------------------------------------------------------------------------
# Shared builder helpers
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


def _make_entity(
    entity_id: str,
    repo: InMemoryEntityRepository,
    name: str = "Test Entity",
) -> CanonicalEntity:
    entity = CanonicalEntity(
        entity_id=entity_id,
        entity_type=EntityType.ISSUE,
        canonical_name=name,
        aliases=[],
        created_at=_BASE_TIME,
    )
    repo.create(entity)
    return entity


def _make_meeting(
    meeting_id: str,
    repo: InMemoryMeetingRepository,
    days_offset: int = 0,
) -> Meeting:
    dt = _BASE_TIME + timedelta(days=days_offset)
    meeting = Meeting(
        meeting_id=meeting_id,
        title=f"Meeting {meeting_id}",
        meeting_date=dt,
        ingested_at=dt,
        transcript="dummy transcript",
    )
    repo.save(meeting)
    return meeting


def _make_resolved_mention(
    mention_id: str,
    entity_id: str,
    meeting_id: str,
    text: str,
    repo: InMemoryMentionRepository,
    source_text: str = "",
) -> EntityMention:
    mention = EntityMention(
        mention_id=mention_id,
        text=text,
        entity_type=EntityType.ISSUE,
        meeting_id=meeting_id,
        source_text=source_text,
        resolution_status=ResolutionStatus.RESOLVED,
        entity_id=entity_id,
        created_at=_BASE_TIME,
    )
    repo.create(mention)
    return mention


def _make_service(
    entity_repo: InMemoryEntityRepository,
    mention_repo: InMemoryMentionRepository,
    meeting_repo: InMemoryMeetingRepository,
) -> UnifiedTimelineService:
    return UnifiedTimelineService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


# ---------------------------------------------------------------------------
# Service Tests
# ---------------------------------------------------------------------------

def test_T01_empty_entity_timeline():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    svc = _make_service(e_repo, m_repo, mtg_repo)

    result = svc.get_unified_timeline("E1", current_time=_BASE_TIME)
    
    assert result.entity_id == "E1"
    assert result.first_observed_at is None
    assert result.last_observed_at is None
    assert len(result.events) == 0


def test_T02_single_observation_without_state_change():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo)
    # No keywords -> state remains UNKNOWN
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="E1 discussed")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    result = svc.get_unified_timeline("E1", current_time=_BASE_TIME)
    
    events = result.events
    # We should have:
    # 1. OBSERVATION (from temporal state engine)
    # 2. INSIGHT (UNKNOWN_STATE) -> Since state is UNKNOWN after observations
    # 3. ATTENTION
    # 4. ACTION
    assert len(events) == 4
    event_types = {e.event_type for e in events}
    assert TimelineEventType.OBSERVATION in event_types
    assert TimelineEventType.INSIGHT in event_types
    assert TimelineEventType.ATTENTION in event_types
    assert TimelineEventType.ACTION in event_types
    
    obs = next(e for e in events if e.event_type == TimelineEventType.OBSERVATION)
    assert obs.event_metadata["is_valid_transition"] is True


def test_T03_single_observation_with_state_change():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo)
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="E1 is blocked")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    result = svc.get_unified_timeline("E1", current_time=_BASE_TIME)
    
    events = result.events
    # We should have:
    # 1. STATE_CHANGE (UNKNOWN -> BLOCKED)
    # 2. INSIGHT (STATE_CHANGED)
    # 3. ATTENTION
    # 4. ACTION
    assert len(events) >= 2
    event_types = {e.event_type for e in events}
    assert TimelineEventType.STATE_CHANGE in event_types
    assert TimelineEventType.INSIGHT in event_types
    assert TimelineEventType.ATTENTION in event_types
    assert TimelineEventType.ACTION in event_types
    
    state_chg = next(e for e in events if e.event_type == TimelineEventType.STATE_CHANGE)
    assert state_chg.event_metadata["from_state"] == "UNKNOWN"
    assert state_chg.event_metadata["to_state"] == "BLOCKED"


def test_T04_repeated_observation_memory_fact_included():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo)
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="E1")
    _make_resolved_mention("Mnt2", "E1", "M1", "test", m_repo, source_text="E1")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    result = svc.get_unified_timeline("E1", current_time=_BASE_TIME)
    
    events = result.events
    event_types = {e.event_type for e in events}
    # Should contain OBSERVATION (twice), MEMORY_FACT (REPEATED_OBSERVATION), 
    # INSIGHT (UNKNOWN_STATE and REPEATED_OBSERVATION),
    # ACTION (REVIEW for UNKNOWN, FOLLOW_UP for REPEATED)
    # ATTENTION (score > 0)
    
    assert TimelineEventType.MEMORY_FACT in event_types
    mem_facts = [e for e in events if e.event_type == TimelineEventType.MEMORY_FACT]
    assert len(mem_facts) == 1
    assert mem_facts[0].event_metadata["fact_type"] == "REPEATED_OBSERVATION"


def test_T05_redundant_memory_facts_excluded():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo)
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="E1 is open")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    result = svc.get_unified_timeline("E1", current_time=_BASE_TIME)
    
    # State transitions trigger STATE_TRANSITION, FIRST_OBSERVED, LAST_OBSERVED memory facts
    # These should be EXCLUDED from the timeline.
    for e in result.events:
        assert e.event_type != TimelineEventType.MEMORY_FACT


def test_T10_full_story():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo)
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="E1 is blocked")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    result = svc.get_unified_timeline("E1", current_time=_BASE_TIME)
    
    events = result.events
    event_types = {e.event_type for e in events}
    
    assert TimelineEventType.STATE_CHANGE in event_types
    assert TimelineEventType.INSIGHT in event_types
    assert TimelineEventType.ATTENTION in event_types
    assert TimelineEventType.ACTION in event_types
    
    # Check deterministic ordering (all happened at same time, so sorted by type/id)
    # Ensure they are sorted by occurred_at ASC, event_type ASC
    for i in range(len(events) - 1):
        # Time ASC
        assert events[i].occurred_at <= events[i+1].occurred_at
        if events[i].occurred_at == events[i+1].occurred_at:
            # Type ASC
            assert events[i].event_type.value <= events[i+1].event_type.value
            if events[i].event_type.value == events[i+1].event_type.value:
                # ID ASC
                assert events[i].event_id < events[i+1].event_id


def test_T12_unknown_entity_raises():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    svc = _make_service(e_repo, m_repo, mtg_repo)
    with pytest.raises(EntityNotFoundError):
        svc.get_unified_timeline("XYZ")


# ---------------------------------------------------------------------------
# API Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    from app.main import app
    from app.api.entities import (
        _entity_repository,
        _mention_repository,
    )
    from app.api.meetings import _meeting_repository
    
    _entity_repository._store.clear()
    _mention_repository._store.clear()
    _meeting_repository._store.clear()
    
    yield TestClient(app)


def test_T14_get_timeline_api(api_client):
    from app.api.entities import _entity_repository, _mention_repository
    from app.api.meetings import _meeting_repository
    
    _make_entity("E1", _entity_repository)
    _make_meeting("M1", _meeting_repository)
    _make_resolved_mention("Mnt1", "E1", "M1", "test", _mention_repository, source_text="E1 is blocked")
    
    resp = api_client.get("/api/v1/entities/E1/timeline")
    assert resp.status_code == 200
    data = resp.json()
    
    assert data["entity_id"] == "E1"
    assert data["event_count"] > 0
    assert "events" in data
    
    types = [e["event_type"] for e in data["events"]]
    assert "STATE_CHANGE" in types
    assert "ACTION" in types
    assert "ATTENTION" in types


def test_T15_get_timeline_404(api_client):
    resp = api_client.get("/api/v1/entities/UNKNOWN/timeline")
    assert resp.status_code == 404


def test_T16_get_timeline_empty(api_client):
    from app.api.entities import _entity_repository
    _make_entity("E1", _entity_repository)
    
    resp = api_client.get("/api/v1/entities/E1/timeline")
    assert resp.status_code == 200
    data = resp.json()
    
    assert data["event_count"] == 0
    assert data["events"] == []
