"""Tests for the Action Recommendation Engine (Stage 10 of the pipeline).

All tests are fully deterministic -- no LLM calls, no network, no external database.

Coverage
--------

Service unit tests (no HTTP):
  R01. Entity with no attention score produces empty actions (Rule G).
  R02. ISSUE_BLOCKED generates ESCALATE (Rule A).
  R03. STALE_ENTITY generates REQUEST_UPDATE (Rule B).
  R04. REOPEN_ATTEMPT generates INVESTIGATE (Rule C).
  R05. REPEATED_OBSERVATION generates FOLLOW_UP (Rule E).
  R06. STATE_CHANGED generates REVIEW (Rule F).
  R07. Deterministic IDs and stable ordering.
  R08. Deduplication: multiple repeated observations group into one FOLLOW_UP action.
  R09. Multiple rules firing on same entity generate separate actions.
  R10. Unknown entity_id raises EntityNotFoundError.

API endpoint tests (full stack via TestClient):
  R11. GET /api/v1/entities/{id}/actions returns recommended actions.
  R12. GET /api/v1/entities/{id}/actions returns empty list for zero score.
  R13. GET /api/v1/entities/{id}/actions returns 404 for unknown entity.
"""

from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models.actions import ActionPriority, ActionType
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
from app.services.action_recommendation_service import ActionRecommendationService
from app.services.entity_service import EntityNotFoundError
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
) -> ActionRecommendationService:
    return ActionRecommendationService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


# ---------------------------------------------------------------------------
# Service Tests
# ---------------------------------------------------------------------------

def test_R01_no_attention_produces_empty_actions():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    svc = _make_service(e_repo, m_repo, mtg_repo)

    result = svc.get_entity_actions("E1", current_time=_BASE_TIME)
    assert len(result) == 0


def test_R02_issue_blocked_generates_escalate():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo)
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="E1 is currently blocked")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    actions = svc.get_entity_actions("E1", current_time=_BASE_TIME)
    
    assert len(actions) == 2
    # Expect ESCALATE (from BLOCKED) and REVIEW (from STATE_CHANGED)
    action_types = {a.action_type for a in actions}
    assert ActionType.ESCALATE in action_types
    assert ActionType.REVIEW in action_types
    
    escalate = next(a for a in actions if a.action_type == ActionType.ESCALATE)
    assert escalate.priority == ActionPriority.CRITICAL


def test_R03_stale_entity_generates_request_update():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo, days_offset=-40) # 40 days ago
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="E1 is identified")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    actions = svc.get_entity_actions("E1", current_time=_BASE_TIME)
    
    action_types = {a.action_type for a in actions}
    assert ActionType.REQUEST_UPDATE in action_types
    req_update = next(a for a in actions if a.action_type == ActionType.REQUEST_UPDATE)
    assert req_update.priority == ActionPriority.HIGH


def test_R04_reopen_attempt_generates_investigate():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo, days_offset=0)
    _make_meeting("M2", mtg_repo, days_offset=1)
    
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="E1 is resolved")
    _make_resolved_mention("Mnt2", "E1", "M2", "test", m_repo, source_text="E1 is in progress")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    actions = svc.get_entity_actions("E1", current_time=_BASE_TIME + timedelta(days=2))
    
    action_types = {a.action_type for a in actions}
    assert ActionType.INVESTIGATE in action_types
    investigate = next(a for a in actions if a.action_type == ActionType.INVESTIGATE)
    assert investigate.priority == ActionPriority.HIGH


def test_R05_repeated_observation_generates_follow_up():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo)
    
    # 3 mentions in same meeting = REPEATED_OBSERVATION
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="just E1")
    _make_resolved_mention("Mnt2", "E1", "M1", "test", m_repo, source_text="E1 again")
    _make_resolved_mention("Mnt3", "E1", "M1", "test", m_repo, source_text="E1 still here")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    actions = svc.get_entity_actions("E1", current_time=_BASE_TIME)
    
    action_types = {a.action_type for a in actions}
    assert ActionType.FOLLOW_UP in action_types
    
    # UNKNOWN_STATE also produces REVIEW.
    assert ActionType.REVIEW in action_types


def test_R07_deterministic_ids_and_sort():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo)
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="E1 is blocked")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    actions1 = svc.get_entity_actions("E1", current_time=_BASE_TIME)
    actions2 = svc.get_entity_actions("E1", current_time=_BASE_TIME)
    
    assert [a.action_id for a in actions1] == [a.action_id for a in actions2]
    # Check stable sort key
    assert "CRITICAL" not in actions1[0].deterministic_sort_key # Uses numeric weight
    assert actions1[0].deterministic_sort_key.startswith("4|") # CRITICAL weight
    
    # Check ordering is CRITICAL then REVIEW (priority 4 then 2)
    assert actions1[0].action_type == ActionType.ESCALATE
    assert actions1[1].action_type == ActionType.REVIEW


def test_R08_deduplication():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    _make_entity("E1", e_repo)
    _make_meeting("M1", mtg_repo)
    _make_meeting("M2", mtg_repo, days_offset=1)
    
    # Repeated observation in M1
    _make_resolved_mention("Mnt1", "E1", "M1", "test", m_repo, source_text="E1")
    _make_resolved_mention("Mnt2", "E1", "M1", "test", m_repo, source_text="E1")
    
    # Repeated observation in M2
    _make_resolved_mention("Mnt3", "E1", "M2", "test", m_repo, source_text="E1")
    _make_resolved_mention("Mnt4", "E1", "M2", "test", m_repo, source_text="E1")
    
    svc = _make_service(e_repo, m_repo, mtg_repo)
    actions = svc.get_entity_actions("E1", current_time=_BASE_TIME + timedelta(days=2))
    
    follow_up_actions = [a for a in actions if a.action_type == ActionType.FOLLOW_UP]
    # Should deduplicate into exactly one FOLLOW_UP action, referencing both insights.
    assert len(follow_up_actions) == 1
    action = follow_up_actions[0]
    assert len(action.related_insight_ids) == 2


def test_R10_unknown_entity_raises():
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    svc = _make_service(e_repo, m_repo, mtg_repo)
    with pytest.raises(EntityNotFoundError):
        svc.get_entity_actions("XYZ")


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


def test_R11_get_entity_actions(api_client):
    from app.api.entities import _entity_repository, _mention_repository
    from app.api.meetings import _meeting_repository
    
    _make_entity("E1", _entity_repository)
    _make_meeting("M1", _meeting_repository)
    _make_resolved_mention("Mnt1", "E1", "M1", "test", _mention_repository, source_text="E1 is blocked")
    
    resp = api_client.get("/api/v1/entities/E1/actions")
    assert resp.status_code == 200
    data = resp.json()
    
    assert data["entity_id"] == "E1"
    assert data["action_count"] >= 2
    
    types = [a["action_type"] for a in data["actions"]]
    assert "ESCALATE" in types
    assert "REVIEW" in types


def test_R12_get_entity_actions_zero_score(api_client):
    from app.api.entities import _entity_repository
    _make_entity("E1", _entity_repository)
    
    resp = api_client.get("/api/v1/entities/E1/actions")
    assert resp.status_code == 200
    data = resp.json()
    
    assert data["action_count"] == 0
    assert data["actions"] == []


def test_R13_get_entity_actions_404(api_client):
    resp = api_client.get("/api/v1/entities/UNKNOWN/actions")
    assert resp.status_code == 404
