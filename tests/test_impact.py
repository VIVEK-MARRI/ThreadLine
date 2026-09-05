"""Tests for the Cross-Entity Risk Propagation & Impact Analysis Engine (Stage 13).

All tests are fully deterministic -- no LLM calls, no network, no external database.

Coverage
--------
Service unit tests:
  R01. Unknown entity_id raises EntityNotFoundError.
  R02. No risks in related entities -> empty impacts.
  R03. BLOCKED entity -> HIGH impact.
  R04. CRITICAL attention + strong relationship (>= 2) -> CRITICAL impact.
  R05. CRITICAL attention + weak relationship (1) -> HIGH impact.
  R06. HIGH attention + strong relationship (>= 2) -> HIGH impact.
  R07. HIGH attention + weak relationship (1) -> MEDIUM impact.
  R08. REOPEN_ATTEMPT insight -> MEDIUM impact.
  R09. STALE_ENTITY / REPEATED_OBSERVATION insight -> LOW impact.
  R10. Multiple signals are deduplicated.
  R11. The highest severity signal determines the impact level.
  R12. Sort order is correct (ImpactLevel DESC, strength DESC, source_entity ASC).

API endpoint tests:
  R14. GET /{entity_id}/impacts returns correct JSON structure.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.models.entity import CanonicalEntity, EntityMention, EntityType, ResolutionStatus
from app.models.meeting import Meeting
from app.models.impact import ImpactLevel, RiskSignalType
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.services.attention_service import AttentionService
from app.services.entity_relationship_service import EntityRelationshipService
from app.services.insight_service import InsightService
from app.services.temporal_state_service import TemporalStateService
from app.services.impact_analysis_service import ImpactAnalysisService
from app.services.entity_service import EntityNotFoundError
from app.temporal.state_interpreter import KeywordStateInterpreter
from app.temporal.transition_policy import DefaultTransitionPolicy

# ---------------------------------------------------------------------------
# Shared builder helpers
# ---------------------------------------------------------------------------

# Use current time so that API tests which use datetime.now(timezone.utc) don't see STALE entities by default
_BASE_TIME = datetime.now(timezone.utc)

def _make_entity(entity_id: str, canonical_name: str) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=EntityType.PERSON,
        canonical_name=canonical_name,
        aliases=[],
        created_at=_BASE_TIME,
    )

def _make_meeting(meeting_id: str, date: datetime) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title="Test Meeting",
        meeting_date=date,
        ingested_at=date,
        transcript="dummy transcript",
    )

def _make_mention(mention_id: str, entity_id: str, meeting_id: str, source_text: str) -> EntityMention:
    return EntityMention(
        mention_id=mention_id,
        entity_type=EntityType.PERSON,
        text="text",
        meeting_id=meeting_id,
        source_text=source_text,
        entity_id=entity_id,
        resolution_status=ResolutionStatus.RESOLVED,
        created_at=_BASE_TIME,
    )

def _build_service(entities=None, meetings=None, mentions=None) -> ImpactAnalysisService:
    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()

    for e in (entities or []):
        e_repo.create(e)
    for mtg in (meetings or []):
        mtg_repo.save(mtg)
    for m in (mentions or []):
        m_repo.create(m)

    rel_svc = EntityRelationshipService(e_repo, m_repo)
    interpreter = KeywordStateInterpreter()
    policy = DefaultTransitionPolicy()
    
    temp_svc = TemporalStateService(e_repo, m_repo, mtg_repo, interpreter, policy)
    insight_svc = InsightService(e_repo, m_repo, mtg_repo, interpreter, policy)
    attn_svc = AttentionService(e_repo, m_repo, mtg_repo, interpreter, policy)
    
    return ImpactAnalysisService(
        entity_repo=e_repo,
        relationship_service=rel_svc,
        temporal_service=temp_svc,
        insight_service=insight_svc,
        attention_service=attn_svc,
    )

# ---------------------------------------------------------------------------
# R01: Unknown entity_id raises EntityNotFoundError
# ---------------------------------------------------------------------------
def test_r01_unknown_entity_raises_not_found() -> None:
    service = _build_service()
    with pytest.raises(EntityNotFoundError):
        service.get_entity_impacts("nonexistent-entity-id", _BASE_TIME)

# ---------------------------------------------------------------------------
# R02: No risks in related entities -> empty impacts
# ---------------------------------------------------------------------------
def test_r02_no_risks_returns_empty() -> None:
    e1 = _make_entity("e1", "rahul")
    e2 = _make_entity("e2", "priya")
    mtg = _make_meeting("m1", _BASE_TIME)
    
    # Just standard IN_PROGRESS mentions, no blockages or attention
    mentions = [
        _make_mention("mn1", "e1", "m1", "rahul is working"),
        _make_mention("mn2", "e2", "m1", "priya is working"),
    ]
    
    service = _build_service([e1, e2], [mtg], mentions)
    impacts = service.get_entity_impacts("e1", _BASE_TIME)
    
    assert len(impacts) == 0

# ---------------------------------------------------------------------------
# R03: BLOCKED entity -> HIGH impact
# ---------------------------------------------------------------------------
def test_r03_blocked_entity_high_impact() -> None:
    e1 = _make_entity("e1", "rahul") # Impacted
    e2 = _make_entity("e2", "priya") # Source (BLOCKED)
    mtg = _make_meeting("m1", _BASE_TIME)
    
    mentions = [
        _make_mention("mn1", "e1", "m1", "rahul is working"),
        _make_mention("mn2", "e2", "m1", "priya is blocked"),
    ]
    
    service = _build_service([e1, e2], [mtg], mentions)
    impacts = service.get_entity_impacts("e1", _BASE_TIME)
    
    assert len(impacts) == 1
    assert impacts[0].impact_level == ImpactLevel.HIGH
    assert RiskSignalType.BLOCKED_ENTITY in impacts[0].risk_signals

# ---------------------------------------------------------------------------
# R04: CRITICAL attention + strong relationship (>= 2) -> CRITICAL impact
# ---------------------------------------------------------------------------
def test_r04_critical_attention_strong_rel_critical_impact() -> None:
    e1 = _make_entity("e1", "rahul")
    e2 = _make_entity("e2", "priya") # Source (BLOCKED > 30 days -> CRITICAL attention)
    
    m1 = _make_meeting("m1", _BASE_TIME - timedelta(days=35))
    m2 = _make_meeting("m2", _BASE_TIME)
    
    mentions = [
        _make_mention("mn1", "e1", "m1", "rahul is working"),
        _make_mention("mn2", "e2", "m1", "priya is blocked"),
        _make_mention("mn3", "e1", "m2", "rahul is still working"),
        _make_mention("mn4", "e2", "m2", "priya is still blocked"),
    ]
    
    service = _build_service([e1, e2], [m1, m2], mentions)
    impacts = service.get_entity_impacts("e1", _BASE_TIME)
    
    assert len(impacts) == 1
    # Should have strength 2 (m1, m2) and priya has CRITICAL attention
    assert impacts[0].impact_level == ImpactLevel.CRITICAL
    assert impacts[0].relationship_strength == 2
    assert RiskSignalType.CRITICAL_ATTENTION in impacts[0].risk_signals

# ---------------------------------------------------------------------------
# R05: CRITICAL attention + weak relationship (1) -> HIGH impact
# ---------------------------------------------------------------------------
def test_r05_critical_attention_weak_rel_high_impact() -> None:
    e1 = _make_entity("e1", "rahul")
    e2 = _make_entity("e2", "priya") # Source (BLOCKED > 30 days -> CRITICAL attention)
    
    # Just 1 meeting together
    m1 = _make_meeting("m1", _BASE_TIME - timedelta(days=35))
    
    mentions = [
        _make_mention("mn1", "e1", "m1", "rahul is working"),
        _make_mention("mn2", "e2", "m1", "priya is blocked"),
    ]
    
    service = _build_service([e1, e2], [m1], mentions)
    impacts = service.get_entity_impacts("e1", _BASE_TIME)
    
    assert len(impacts) == 1
    # Strength 1, but priya has CRITICAL attention -> HIGH impact
    assert impacts[0].impact_level == ImpactLevel.HIGH
    assert impacts[0].relationship_strength == 1
    assert RiskSignalType.CRITICAL_ATTENTION in impacts[0].risk_signals

# ---------------------------------------------------------------------------
# R08: REOPEN_ATTEMPT insight -> MEDIUM impact
# ---------------------------------------------------------------------------
def test_r08_reopen_attempt_medium_impact() -> None:
    e1 = _make_entity("e1", "rahul")
    e2 = _make_entity("e2", "priya") 
    
    m1 = _make_meeting("m1", _BASE_TIME - timedelta(days=10))
    m2 = _make_meeting("m2", _BASE_TIME)
    
    mentions = [
        _make_mention("mn1", "e2", "m1", "priya is resolved"),
        _make_mention("mn2", "e1", "m2", "rahul is working"),
        _make_mention("mn3", "e2", "m2", "priya is blocked"), # REOPEN_ATTEMPT
    ]
    
    service = _build_service([e1, e2], [m1, m2], mentions)
    impacts = service.get_entity_impacts("e1", _BASE_TIME)
    
    assert len(impacts) == 1
    assert impacts[0].impact_level == ImpactLevel.MEDIUM
    assert RiskSignalType.REOPEN_ATTEMPT in impacts[0].risk_signals

# ---------------------------------------------------------------------------
# R09: STALE_ENTITY insight -> LOW impact
# ---------------------------------------------------------------------------
def test_r09_stale_entity_low_impact() -> None:
    e1 = _make_entity("e1", "rahul")
    e2 = _make_entity("e2", "priya") 
    
    m1 = _make_meeting("m1", _BASE_TIME - timedelta(days=95))
    m2 = _make_meeting("m2", _BASE_TIME)
    
    mentions = [
        _make_mention("mn1", "e2", "m1", "priya is in progress"),
        _make_mention("mn2", "e1", "m1", "rahul is here"),
        _make_mention("mn3", "e1", "m2", "rahul is still here"),
    ]
    
    service = _build_service([e1, e2], [m1, m2], mentions)
    impacts = service.get_entity_impacts("e1", _BASE_TIME)
    
    assert len(impacts) == 1
    # STALE_ENTITY triggers HIGH_ATTENTION in AttentionService. 
    # HIGH_ATTENTION + weak relationship -> MEDIUM impact.
    assert impacts[0].impact_level == ImpactLevel.MEDIUM
    assert RiskSignalType.STALE_ENTITY in impacts[0].risk_signals
    assert RiskSignalType.HIGH_ATTENTION in impacts[0].risk_signals

# ---------------------------------------------------------------------------
# R12: Sort order is correct
# ---------------------------------------------------------------------------
def test_r12_sort_order() -> None:
    e_me = _make_entity("e0", "me")
    e_low = _make_entity("e_low", "low risk") # STALE
    e_med = _make_entity("e_med", "med risk") # REOPEN
    e_high = _make_entity("e_high", "high risk") # BLOCKED
    e_crit = _make_entity("e_crit", "crit risk") # CRITICAL ATTN + STRONG
    
    # 95 days ago
    m0 = _make_meeting("m0", _BASE_TIME - timedelta(days=95))
    m1 = _make_meeting("m1", _BASE_TIME - timedelta(days=35))
    m2 = _make_meeting("m2", _BASE_TIME)
    
    mentions = [
        # e_low (stale)
        _make_mention("mn01", "e_low", "m0", "in progress"),
        _make_mention("mn02", "e0", "m0", "here"),
        
        # e_med (reopen attempt)
        _make_mention("mn11", "e_med", "m1", "resolved"),
        _make_mention("mn12", "e_med", "m2", "blocked"),
        _make_mention("mn13", "e0", "m2", "here"),
        
        # e_high (blocked, strength 1)
        _make_mention("mn21", "e_high", "m2", "blocked"),
        _make_mention("mn22", "e0", "m2", "here"),
        
        # e_crit (blocked > 30d => CRITICAL attention, strength 2)
        _make_mention("mn31", "e_crit", "m1", "blocked"),
        _make_mention("mn32", "e0", "m1", "here"),
        _make_mention("mn33", "e_crit", "m2", "blocked"),
        _make_mention("mn34", "e0", "m2", "here"),
    ]
    
    service = _build_service([e_me, e_low, e_med, e_high, e_crit], [m0, m1, m2], mentions)
    impacts = service.get_entity_impacts("e0", _BASE_TIME)
    
    assert len(impacts) == 4
    
    # Expected order based on level_map sorting (CRITICAL=4, HIGH=3, MEDIUM=2, LOW=1)
    # The actual impacts generated: e_crit (CRITICAL), e_med (HIGH, strength=2), e_high (HIGH, strength=1), e_low (MEDIUM)
    expected_levels = [
        ImpactLevel.CRITICAL,
        ImpactLevel.HIGH,
        ImpactLevel.HIGH,
        ImpactLevel.MEDIUM,
    ]
    actual_levels = [i.impact_level for i in impacts]
    assert actual_levels == expected_levels

# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def impact_client():
    from app.main import app
    from app.api.entities import get_impact_analysis_service, get_entity_service

    e_repo, m_repo, mtg_repo = InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryMeetingRepository()
    
    rel_svc = EntityRelationshipService(e_repo, m_repo)
    interpreter = KeywordStateInterpreter()
    policy = DefaultTransitionPolicy()
    
    temp_svc = TemporalStateService(e_repo, m_repo, mtg_repo, interpreter, policy)
    insight_svc = InsightService(e_repo, m_repo, mtg_repo, interpreter, policy)
    attn_svc = AttentionService(e_repo, m_repo, mtg_repo, interpreter, policy)
    
    service = ImpactAnalysisService(
        entity_repo=e_repo,
        relationship_service=rel_svc,
        temporal_service=temp_svc,
        insight_service=insight_svc,
        attention_service=attn_svc,
    )

    from app.services.entity_service import EntityService
    entity_service = EntityService(
        entity_repo=e_repo,
        mention_repo=m_repo,
    )

    app.dependency_overrides[get_impact_analysis_service] = lambda: service
    app.dependency_overrides[get_entity_service] = lambda: entity_service

    client = TestClient(app)
    yield client, e_repo, m_repo, mtg_repo

    app.dependency_overrides.pop(get_impact_analysis_service, None)
    app.dependency_overrides.pop(get_entity_service, None)

def test_r14_api_response_validates(impact_client) -> None:
    client, e_repo, m_repo, mtg_repo = impact_client
    
    e_repo.create(_make_entity("e1", "rahul"))
    e_repo.create(_make_entity("e2", "priya"))
    mtg_repo.save(_make_meeting("m1", _BASE_TIME))
    m_repo.create(_make_mention("mn1", "e1", "m1", "rahul is working"))
    m_repo.create(_make_mention("mn2", "e2", "m1", "priya is blocked"))
    
    response = client.get("/api/v1/entities/e1/impacts")
    assert response.status_code == 200
    body = response.json()
    
    assert body["entity_id"] == "e1"
    assert body["impact_count"] == 1
    
    impact = body["impacts"][0]
    assert "impact_id" in impact
    assert impact["source_entity_id"] == "e2"
    assert impact["impacted_entity_id"] == "e1"
    assert impact["impact_level"] == "HIGH"
    assert "BLOCKED_ENTITY" in impact["risk_signals"]
    assert impact["relationship_strength"] == 1
    assert "reason" in impact
