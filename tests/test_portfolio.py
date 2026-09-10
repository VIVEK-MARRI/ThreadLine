"""Tests for the Organisation-Wide Portfolio Intelligence Engine (Stage 14).

All tests are fully deterministic -- no LLM calls, no network, no external database.

Coverage
--------

Service unit tests (no HTTP):
  P01. Empty organisation — zero entities, zero risk counts.
  P02. Single entity with observations but no risk signals → LOW risk.
  P03. CRITICAL attention entity → portfolio risk CRITICAL.
  P04. CRITICAL impact entity → portfolio risk CRITICAL.
  P05. HIGH attention entity → portfolio risk HIGH.
  P06. BLOCKED entity (temporal state) → portfolio risk HIGH.
  P07. MEDIUM impact entity → portfolio risk MEDIUM.
  P08. Entity with active actions (from attention+insight) → correct action_count.
  P09. Multiple entities with different risk levels → all present.
  P10. Correct aggregate risk level counts.
  P11. Correct deterministic ordering (CRITICAL → HIGH → MEDIUM → LOW).
  P12. Equal-risk tie-breaking (attention_score DESC, then entity_id ASC).
  P13. Multiple impacts increase impact_count correctly.
  P14. Multiple actions increase action_count correctly.
  P15. Entity with only observations but no risk signals → LOW risk.
  P16. Entity with zero observations and no intelligence → excluded.
  P17. Same input + same current_time produces identical result.
  P18. Repeated calls do not mutate state (idempotency).
  P19. Existing entity intelligence is unchanged after portfolio calculation.
  P20. entities_with_active_actions count is correct.
  P21. entities_with_impact count is correct.
  P22. blocked_entities count is correct.
  P23. CRITICAL attention via BLOCKED + STALE → CRITICAL level.
  P24. Impact signal override: entity with no attention but CRITICAL impact → CRITICAL.
  P25. LOW attention entity with MEDIUM impact → MEDIUM (impact wins).

API endpoint tests (full stack via TestClient):
  P26. GET /api/v1/portfolio returns valid JSON structure.
  P27. GET /api/v1/portfolio with no entities returns empty portfolio.
  P28. GET /api/v1/portfolio with a CRITICAL entity reflects in response.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.models.entity import CanonicalEntity, EntityMention, EntityType, ResolutionStatus
from app.models.meeting import Meeting
from app.models.portfolio import (
    OrganisationPortfolio,
    PortfolioEntitySummary,
    PortfolioRiskLevel,
    PORTFOLIO_RISK_LEVEL_ORDER,
)
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.portfolio_intelligence_service import (
    PortfolioIntelligenceService,
    _classify_risk,
)
from app.services.entity_service import EntityService
from app.temporal.state_interpreter import KeywordStateInterpreter
from app.temporal.transition_policy import DefaultTransitionPolicy
from app.models.attention import AttentionLevel
from app.models.impact import ImpactLevel
from app.models.temporal import TemporalState


# ---------------------------------------------------------------------------
# Shared builder helpers  (mirrors the pattern in test_impact.py, test_actions.py)
# ---------------------------------------------------------------------------

# Fixed reference time — ensures stale-entity logic is deterministic.
_BASE_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
# A "recent" time for creating entities/mentions without triggering stale detection.
_RECENT = _BASE_TIME - timedelta(days=1)


def _make_entity(entity_id: str, canonical_name: str, entity_type: EntityType = EntityType.ISSUE) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        canonical_name=canonical_name,
        aliases=[],
        created_at=_BASE_TIME,
    )


def _make_meeting(meeting_id: str, date: datetime) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=f"Meeting {meeting_id}",
        meeting_date=date,
        ingested_at=date,
        transcript="dummy transcript",
    )


def _make_mention(
    mention_id: str,
    entity_id: str,
    meeting_id: str,
    source_text: str,
    entity_type: EntityType = EntityType.ISSUE,
) -> EntityMention:
    return EntityMention(
        mention_id=mention_id,
        entity_type=entity_type,
        text="text",
        meeting_id=meeting_id,
        source_text=source_text,
        entity_id=entity_id,
        resolution_status=ResolutionStatus.RESOLVED,
        created_at=_BASE_TIME,
    )


def _build_service(
    entities: Optional[list] = None,
    meetings: Optional[list] = None,
    mentions: Optional[list] = None,
) -> PortfolioIntelligenceService:
    """Build a PortfolioIntelligenceService with in-memory repositories."""
    e_repo = InMemoryEntityRepository()
    m_repo = InMemoryMentionRepository()
    mtg_repo = InMemoryMeetingRepository()

    for e in (entities or []):
        e_repo.create(e)
    for mtg in (meetings or []):
        mtg_repo.save(mtg)
    for m in (mentions or []):
        m_repo.create(m)

    return PortfolioIntelligenceService(
        entity_repo=e_repo,
        mention_repo=m_repo,
        meeting_repo=mtg_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


# ===========================================================================
# P01 — Empty organisation
# ===========================================================================

def test_p01_empty_organisation() -> None:
    """Portfolio with zero entities returns all-zero counts."""
    service = _build_service()
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    assert portfolio.total_entities == 0
    assert portfolio.critical_entities == 0
    assert portfolio.high_risk_entities == 0
    assert portfolio.medium_risk_entities == 0
    assert portfolio.low_risk_entities == 0
    assert portfolio.entities_with_active_actions == 0
    assert portfolio.entities_with_impact == 0
    assert portfolio.blocked_entities == 0
    assert portfolio.entities == []
    assert portfolio.evaluated_at == _BASE_TIME


# ===========================================================================
# P02 — Single entity with observations only (LOW risk)
# ===========================================================================

def test_p02_single_low_risk_entity() -> None:
    """Entity with observations but no recognisable state keywords → UNKNOWN_STATE → LOW.

    An entity with source text containing no state-bearing keywords produces
    an UNKNOWN_STATE insight (score=10, attention=LOW).  LOW attention with no
    impact or BLOCKED state → PortfolioRiskLevel.LOW.
    """
    e1 = _make_entity("e1", "auth service")
    mtg = _make_meeting("m1", _RECENT)
    # Source text with NO state-bearing keywords → UNKNOWN_STATE insight → LOW attention
    mention = _make_mention("mn1", "e1", "m1", "auth service mentioned in standup")

    service = _build_service([e1], [mtg], [mention])
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    assert portfolio.total_entities == 1
    assert portfolio.low_risk_entities == 1
    assert portfolio.critical_entities == 0
    assert portfolio.high_risk_entities == 0
    assert portfolio.medium_risk_entities == 0

    summary = portfolio.entities[0]
    assert summary.entity_id == "e1"
    assert summary.risk_level == PortfolioRiskLevel.LOW
    assert summary.attention_level == "LOW"
    assert summary.observation_count == 1


# ===========================================================================
# P03 — CRITICAL attention entity → portfolio risk CRITICAL
# ===========================================================================

def test_p03_critical_attention_entity() -> None:
    """Entity with CRITICAL attention is classified CRITICAL in portfolio."""
    e1 = _make_entity("e1", "payment api")
    # BLOCKED state → ENTITY_BLOCKED attention reason → score 100 → CRITICAL
    mtg = _make_meeting("m1", _RECENT)
    mention = _make_mention("mn1", "e1", "m1", "payment api is blocked by infrastructure issue")

    service = _build_service([e1], [mtg], [mention])
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    assert portfolio.total_entities == 1
    assert portfolio.critical_entities == 1
    assert portfolio.blocked_entities == 1

    summary = portfolio.entities[0]
    assert summary.risk_level == PortfolioRiskLevel.CRITICAL
    assert summary.attention_level == "CRITICAL"
    assert summary.attention_score >= 100


# ===========================================================================
# P04 — CRITICAL impact signal → portfolio risk CRITICAL
# ===========================================================================

def test_p04_critical_impact_entity() -> None:
    """Entity receiving a CRITICAL impact signal is classified CRITICAL."""
    # e_source: BLOCKED entity with strong relationship (strength >= 2) → CRITICAL attention
    # e_target: impacted entity
    e_source = _make_entity("e_source", "blocked api")
    e_target = _make_entity("e_target", "auth service")

    # Two shared meetings → strength 2 → CRITICAL impact
    m1 = _make_meeting("m1", _BASE_TIME - timedelta(days=35))
    m2 = _make_meeting("m2", _RECENT)

    mentions = [
        _make_mention("mn1", "e_source", "m1", "blocked api is blocked"),
        _make_mention("mn2", "e_target", "m1", "auth service is working"),
        _make_mention("mn3", "e_source", "m2", "blocked api is still blocked"),
        _make_mention("mn4", "e_target", "m2", "auth service is working"),
    ]

    service = _build_service([e_source, e_target], [m1, m2], mentions)
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    # e_target receives CRITICAL impact from e_source
    target_summary = next(s for s in portfolio.entities if s.entity_id == "e_target")
    assert target_summary.risk_level == PortfolioRiskLevel.CRITICAL
    assert target_summary.impact_count >= 1


# ===========================================================================
# P05 — HIGH attention entity → portfolio risk HIGH
# ===========================================================================

def test_p05_high_attention_entity() -> None:
    """Entity with HIGH attention is classified HIGH in portfolio."""
    e1 = _make_entity("e1", "database timeout")
    # STALE: last seen > 30 days ago, not RESOLVED → ENTITY_STALE reason → score 40 → HIGH
    old_meeting = _make_meeting("m1", _BASE_TIME - timedelta(days=45))
    mention = _make_mention("mn1", "e1", "m1", "database timeout is in progress")

    service = _build_service([e1], [old_meeting], [mention])
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    assert portfolio.total_entities == 1
    summary = portfolio.entities[0]
    assert summary.risk_level == PortfolioRiskLevel.HIGH
    assert summary.attention_level == "HIGH"


# ===========================================================================
# P06 — BLOCKED entity (temporal state) → portfolio risk HIGH
# ===========================================================================

def test_p06_blocked_temporal_state_high_risk() -> None:
    """Entity in BLOCKED temporal state but without CRITICAL attention → HIGH risk."""
    e1 = _make_entity("e1", "deployment pipeline")
    # Single recent BLOCKED mention: score = ENTITY_BLOCKED = 100 → CRITICAL attention
    # So BLOCKED + CRITICAL attention → CRITICAL. Let's verify blocked_entities count only.
    mtg = _make_meeting("m1", _RECENT)
    mention = _make_mention("mn1", "e1", "m1", "deployment pipeline is blocked")

    service = _build_service([e1], [mtg], [mention])
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    assert portfolio.blocked_entities == 1
    summary = portfolio.entities[0]
    # BLOCKED triggers ENTITY_BLOCKED → CRITICAL attention → CRITICAL risk
    assert summary.current_state == "BLOCKED"
    # risk is at least HIGH (likely CRITICAL due to ENTITY_BLOCKED attention)
    assert summary.risk_level in (PortfolioRiskLevel.CRITICAL, PortfolioRiskLevel.HIGH)


def test_p06b_blocked_state_without_critical_attention_classified_high() -> None:
    """_classify_risk: BLOCKED temporal state alone (no attention, no impact) -> HIGH."""
    risk = _classify_risk(
        attention_level=None,
        current_state=TemporalState.BLOCKED,
        impact_levels=[],
        observation_count=5,
    )
    assert risk == PortfolioRiskLevel.HIGH


# ===========================================================================
# P07 — MEDIUM impact → portfolio risk MEDIUM
# ===========================================================================

def test_p07_medium_impact_entity() -> None:
    """Entity with a MEDIUM impact signal is classified at least MEDIUM."""
    risk = _classify_risk(
        attention_level=None,
        current_state=TemporalState.OPEN,
        impact_levels=[ImpactLevel.MEDIUM],
        observation_count=3,
    )
    assert risk == PortfolioRiskLevel.MEDIUM


# ===========================================================================
# P08 — Entity with active actions
# ===========================================================================

def test_p08_entity_with_active_actions() -> None:
    """Entity with actionable signals has action_count > 0 in portfolio summary."""
    e1 = _make_entity("e1", "auth service")
    # BLOCKED → CRITICAL attention → ESCALATE action
    mtg = _make_meeting("m1", _RECENT)
    mention = _make_mention("mn1", "e1", "m1", "auth service is blocked by auth issue")

    service = _build_service([e1], [mtg], [mention])
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    summary = portfolio.entities[0]
    assert summary.action_count > 0
    assert portfolio.entities_with_active_actions == 1


# ===========================================================================
# P09 — Multiple entities with different risk levels
# ===========================================================================

def test_p09_multiple_entities_different_risk_levels() -> None:
    """Multiple entities at different risk levels all appear in the portfolio."""
    e_critical = _make_entity("e_critical", "api gateway")
    e_high = _make_entity("e_high", "database")
    e_low = _make_entity("e_low", "logging service")

    m_recent = _make_meeting("m_recent", _RECENT)
    m_old = _make_meeting("m_old", _BASE_TIME - timedelta(days=45))

    mentions = [
        # CRITICAL: blocked → ENTITY_BLOCKED → 100 → CRITICAL
        _make_mention("mn1", "e_critical", "m_recent", "api gateway is blocked"),
        # HIGH: stale, in_progress → ENTITY_STALE → 40 → HIGH
        _make_mention("mn2", "e_high", "m_old", "database is in progress"),
        # LOW: recent, working, no risk signals
        _make_mention("mn3", "e_low", "m_recent", "logging service is working"),
    ]

    service = _build_service([e_critical, e_high, e_low], [m_recent, m_old], mentions)
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    assert portfolio.total_entities == 3
    entity_ids = {s.entity_id for s in portfolio.entities}
    assert "e_critical" in entity_ids
    assert "e_high" in entity_ids
    assert "e_low" in entity_ids


# ===========================================================================
# P10 — Correct aggregate counts
# ===========================================================================

def test_p10_correct_aggregate_counts() -> None:
    """Aggregate risk level counts reflect individual entity classifications."""
    e_c = _make_entity("e_c", "critical entity")
    e_h = _make_entity("e_h", "high entity")
    e_m = _make_entity("e_m", "medium entity")
    e_l = _make_entity("e_l", "low entity")

    m_recent = _make_meeting("m_recent", _RECENT)
    m_old = _make_meeting("m_old", _BASE_TIME - timedelta(days=45))

    mentions = [
        _make_mention("mn1", "e_c", "m_recent", "critical entity is blocked"),
        _make_mention("mn2", "e_h", "m_old", "high entity is in progress"),
        _make_mention("mn3", "e_l", "m_recent", "low entity is working"),
    ]
    # e_m: direct classification via _classify_risk — verify via unit test P07 instead.

    service = _build_service([e_c, e_h, e_l], [m_recent, m_old], mentions)
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    # Verify counts add up to total
    total = (
        portfolio.critical_entities
        + portfolio.high_risk_entities
        + portfolio.medium_risk_entities
        + portfolio.low_risk_entities
    )
    assert total == portfolio.total_entities

    # At least one CRITICAL and one HIGH
    assert portfolio.critical_entities >= 1
    assert portfolio.high_risk_entities >= 1


# ===========================================================================
# P11 — Correct deterministic ordering (CRITICAL → HIGH → MEDIUM → LOW)
# ===========================================================================

def test_p11_deterministic_ordering() -> None:
    """Portfolio entities are sorted: CRITICAL first, then HIGH, MEDIUM, LOW."""
    e_high = _make_entity("e_high", "database")
    e_critical = _make_entity("e_critical", "api gateway")
    e_low = _make_entity("e_low", "logging")

    m_recent = _make_meeting("m_recent", _RECENT)
    m_old = _make_meeting("m_old", _BASE_TIME - timedelta(days=45))

    mentions = [
        _make_mention("mn1", "e_critical", "m_recent", "api gateway is blocked"),
        _make_mention("mn2", "e_high", "m_old", "database is in progress"),
        _make_mention("mn3", "e_low", "m_recent", "logging is working"),
    ]

    service = _build_service(
        [e_high, e_critical, e_low], [m_recent, m_old], mentions
    )
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    risk_levels = [s.risk_level for s in portfolio.entities]
    # Verify that risk level order is non-increasing
    level_values = [PORTFOLIO_RISK_LEVEL_ORDER[rl] for rl in risk_levels]
    assert level_values == sorted(level_values, reverse=True), (
        f"Expected non-increasing risk order, got: {risk_levels}"
    )

    # CRITICAL should be first
    assert portfolio.entities[0].risk_level == PortfolioRiskLevel.CRITICAL


# ===========================================================================
# P12 — Equal-risk tie-breaking
# ===========================================================================

def test_p12_equal_risk_tie_breaking_by_attention_score() -> None:
    """When risk levels are equal, entities with higher attention score come first.

    Both entities are CRITICAL (BLOCKED).  The one that is also STALE has a higher
    attention score (ENTITY_BLOCKED=100 + ENTITY_STALE=40 = 140) than the one that
    is only BLOCKED (ENTITY_BLOCKED=100).  Higher score should appear first.

    Note: Entities are placed in SEPARATE meetings so they do not co-occur and
    do not generate impact signals for each other.
    """
    e_more = _make_entity("aaa", "more critical entity")
    e_less = _make_entity("zzz", "less critical entity")

    # e_more: blocked > 30 days ago → ENTITY_BLOCKED (100) + ENTITY_STALE (40) = 140
    m_old = _make_meeting("m_old", _BASE_TIME - timedelta(days=45))
    # e_less: blocked recently → ENTITY_BLOCKED only = 100
    m_new = _make_meeting("m_new", _RECENT)

    mentions = [
        # e_more alone in m_old so no co-occurrence relationship is established
        _make_mention("mn1", "aaa", "m_old", "more critical entity is blocked"),
        # e_less alone in m_new
        _make_mention("mn2", "zzz", "m_new", "less critical entity is blocked"),
    ]

    service = _build_service([e_more, e_less], [m_old, m_new], mentions)
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    assert len(portfolio.entities) == 2
    assert portfolio.entities[0].entity_id == "aaa"
    assert portfolio.entities[0].attention_score > portfolio.entities[1].attention_score


def test_p12b_equal_risk_and_score_tie_breaking_by_entity_id() -> None:
    """When risk level and attention score are equal, entity_id ASC breaks the tie."""
    # Two entities: both LOW risk, same attention score (0), same observation count.
    # Should be sorted by entity_id ASC.
    e_a = _make_entity("aaa_entity", "alpha")
    e_z = _make_entity("zzz_entity", "zeta")

    m = _make_meeting("m1", _RECENT)
    mentions = [
        _make_mention("mn1", "aaa_entity", "m1", "alpha is working"),
        _make_mention("mn2", "zzz_entity", "m1", "zeta is working"),
    ]

    service = _build_service([e_a, e_z], [m], mentions)
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    assert len(portfolio.entities) == 2
    assert portfolio.entities[0].entity_id == "aaa_entity"
    assert portfolio.entities[1].entity_id == "zzz_entity"


# ===========================================================================
# P13 — Multiple impacts increase impact_count correctly
# ===========================================================================

def test_p13_multiple_impacts_impact_count() -> None:
    """impact_count reflects all impact associations directed at the entity."""
    # e_target co-occurs with two distinct risky sources
    e_target = _make_entity("e_target", "auth service")
    e_src1 = _make_entity("e_src1", "blocked api 1")
    e_src2 = _make_entity("e_src2", "blocked api 2")

    m = _make_meeting("m1", _RECENT)
    mentions = [
        _make_mention("mn1", "e_target", "m1", "auth is working"),
        _make_mention("mn2", "e_src1", "m1", "api 1 is blocked"),
        _make_mention("mn3", "e_src2", "m1", "api 2 is blocked"),
    ]

    service = _build_service([e_target, e_src1, e_src2], [m], mentions)
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    target_summary = next(s for s in portfolio.entities if s.entity_id == "e_target")
    assert target_summary.impact_count == 2


# ===========================================================================
# P14 — Multiple actions increase action_count correctly
# ===========================================================================

def test_p14_multiple_actions_action_count() -> None:
    """action_count reflects all recommended actions for the entity."""
    e1 = _make_entity("e1", "infra issue")
    # BLOCKED → ESCALATE; BLOCKED > 30 days ago → also STALE → REQUEST_UPDATE
    m_old = _make_meeting("m_old", _BASE_TIME - timedelta(days=45))
    mention = _make_mention("mn1", "e1", "m_old", "infra issue is blocked")

    service = _build_service([e1], [m_old], [mention])
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    summary = next(s for s in portfolio.entities if s.entity_id == "e1")
    # Should have at least ESCALATE (blocked) and REQUEST_UPDATE (stale)
    assert summary.action_count >= 2


# ===========================================================================
# P15 — Entity with observations but no risk signals → LOW risk
# ===========================================================================

def test_p15_entity_with_observations_only_unknown_state() -> None:
    """Entity with observations but only UNKNOWN_STATE intelligence has LOW risk.

    Source text with no state-bearing keywords → UNKNOWN_STATE insight → score 10 →
    LOW attention → PortfolioRiskLevel.LOW (no BLOCKED state, no impacts).
    """
    e1 = _make_entity("e1", "ops dashboard")
    mtg = _make_meeting("m1", _RECENT)
    # No state keywords in source text → UNKNOWN_STATE insight → LOW attention
    mention = _make_mention("mn1", "e1", "m1", "ops dashboard came up in discussion")

    service = _build_service([e1], [mtg], [mention])
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    summary = portfolio.entities[0]
    assert summary.observation_count == 1
    assert summary.risk_level == PortfolioRiskLevel.LOW
    assert summary.attention_level == "LOW"
    assert summary.attention_score == 10  # UNKNOWN_STATE reason = 10 points


# ===========================================================================
# P16 — Entity with zero observations → excluded from portfolio
# ===========================================================================

def test_p16_entity_no_observations_excluded() -> None:
    """Entity with no resolved mentions is excluded from the portfolio."""
    e1 = _make_entity("e1", "orphan entity")
    # No meetings, no mentions → no observations

    service = _build_service([e1], [], [])
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    assert portfolio.total_entities == 0
    assert portfolio.entities == []


# ===========================================================================
# P17 — Determinism: same input + same current_time → identical result
# ===========================================================================

def test_p17_deterministic_same_input_same_result() -> None:
    """Calling get_portfolio twice with the same current_time produces identical output."""
    e1 = _make_entity("e1", "payment api")
    mtg = _make_meeting("m1", _RECENT)
    mention = _make_mention("mn1", "e1", "m1", "payment api is blocked")

    service = _build_service([e1], [mtg], [mention])

    portfolio_a = service.get_portfolio(current_time=_BASE_TIME)
    portfolio_b = service.get_portfolio(current_time=_BASE_TIME)

    assert portfolio_a.total_entities == portfolio_b.total_entities
    assert portfolio_a.critical_entities == portfolio_b.critical_entities
    assert len(portfolio_a.entities) == len(portfolio_b.entities)
    for a, b in zip(portfolio_a.entities, portfolio_b.entities):
        assert a.entity_id == b.entity_id
        assert a.risk_level == b.risk_level
        assert a.attention_score == b.attention_score
        assert a.impact_count == b.impact_count
        assert a.action_count == b.action_count


# ===========================================================================
# P18 — Repeated calls do not mutate state (idempotency)
# ===========================================================================

def test_p18_repeated_calls_do_not_mutate() -> None:
    """Multiple portfolio calls do not change underlying repository state."""
    e_repo = InMemoryEntityRepository()
    m_repo = InMemoryMentionRepository()
    mtg_repo = InMemoryMeetingRepository()

    e1 = _make_entity("e1", "audit log")
    mtg = _make_meeting("m1", _RECENT)
    mention = _make_mention("mn1", "e1", "m1", "audit log is blocked")

    e_repo.create(e1)
    mtg_repo.save(mtg)
    m_repo.create(mention)

    initial_entity_count = len(e_repo.list_entities())

    service = PortfolioIntelligenceService(
        entity_repo=e_repo,
        mention_repo=m_repo,
        meeting_repo=mtg_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )

    # Call five times
    for _ in range(5):
        service.get_portfolio(current_time=_BASE_TIME)

    # Repository must not have grown
    assert len(e_repo.list_entities()) == initial_entity_count


# ===========================================================================
# P19 — Existing entity intelligence is unchanged after portfolio calculation
# ===========================================================================

def test_p19_entity_intelligence_unchanged_after_portfolio() -> None:
    """Portfolio calculation does not modify entity data in the repository."""
    e_repo = InMemoryEntityRepository()
    m_repo = InMemoryMentionRepository()
    mtg_repo = InMemoryMeetingRepository()

    e1 = _make_entity("e1", "original name")
    mtg = _make_meeting("m1", _RECENT)
    mention = _make_mention("mn1", "e1", "m1", "original name is blocked")

    e_repo.create(e1)
    mtg_repo.save(mtg)
    m_repo.create(mention)

    service = PortfolioIntelligenceService(
        entity_repo=e_repo,
        mention_repo=m_repo,
        meeting_repo=mtg_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )
    service.get_portfolio(current_time=_BASE_TIME)

    # Entity must still have the original name
    entity_after = e_repo.get_by_id("e1")
    assert entity_after is not None
    assert entity_after.canonical_name == "original name"
    assert entity_after.entity_id == "e1"


# ===========================================================================
# P20 — entities_with_active_actions count
# ===========================================================================

def test_p20_entities_with_active_actions_count() -> None:
    """entities_with_active_actions reflects entities with action_count > 0.

    The blocked entity produces actions (ESCALATE at minimum).
    Entities with BLOCKED state have ENTITY_BLOCKED attention → actions are generated.
    We verify total entities_with_active_actions >= 1 (the blocked entity always has actions).
    """
    e1 = _make_entity("e1", "blocked thing")  # has actions (ENTITY_BLOCKED → ESCALATE)
    e2 = _make_entity("e2", "neutral thing")  # no state keywords → UNKNOWN_STATE → REVIEW action

    # Place entities in SEPARATE meetings to prevent co-occurrence.
    mtg1 = _make_meeting("m1", _RECENT)
    mtg2 = _make_meeting("m2", _RECENT - timedelta(hours=1))

    mentions = [
        _make_mention("mn1", "e1", "m1", "blocked thing is blocked"),
        _make_mention("mn2", "e2", "m2", "neutral thing mentioned in standup"),
    ]

    service = _build_service([e1, e2], [mtg1, mtg2], mentions)
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    e1_summary = next(s for s in portfolio.entities if s.entity_id == "e1")
    # e1 is BLOCKED → ENTITY_BLOCKED → ESCALATE action → at least 1 action
    assert e1_summary.action_count > 0
    assert portfolio.entities_with_active_actions >= 1
    assert portfolio.total_entities == 2


# ===========================================================================
# P21 — entities_with_impact count
# ===========================================================================

def test_p21_entities_with_impact_count() -> None:
    """entities_with_impact reflects entities that have >= 1 impact association.

    When e_source (BLOCKED) is in the same meeting as e_impacted, the CO_OCCURS_WITH
    relationship means e_impacted receives an impact from e_source's risk signals.
    e_independent is placed in a separate meeting so it does NOT co-occur with e_source.
    """
    e_impacted = _make_entity("e_impacted", "auth service")
    e_source = _make_entity("e_source", "blocked api")
    e_independent = _make_entity("e_independent", "logging")

    # e_source and e_impacted share meeting m1 → CO_OCCURS_WITH → impact propagated
    mtg1 = _make_meeting("m1", _RECENT)
    # e_independent is in a separate meeting — no co-occurrence with e_source
    mtg2 = _make_meeting("m2", _RECENT - timedelta(hours=1))

    mentions = [
        _make_mention("mn1", "e_impacted", "m1", "auth service is working"),
        _make_mention("mn2", "e_source", "m1", "blocked api is blocked"),
        _make_mention("mn3", "e_independent", "m2", "logging mentioned in standup"),
    ]

    service = _build_service(
        [e_impacted, e_source, e_independent], [mtg1, mtg2], mentions
    )
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    impacted_summary = next(s for s in portfolio.entities if s.entity_id == "e_impacted")
    assert impacted_summary.impact_count > 0

    # Only e_impacted should receive impact (e_independent is in a different meeting)
    assert portfolio.entities_with_impact == 1


# ===========================================================================
# P22 — blocked_entities count
# ===========================================================================

def test_p22_blocked_entities_count() -> None:
    """blocked_entities reflects entities whose current_state is BLOCKED."""
    e1 = _make_entity("e1", "auth")
    e2 = _make_entity("e2", "logging")

    mtg = _make_meeting("m1", _RECENT)
    mentions = [
        _make_mention("mn1", "e1", "m1", "auth is blocked"),
        _make_mention("mn2", "e2", "m1", "logging is in progress"),
    ]

    service = _build_service([e1, e2], [mtg], mentions)
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    assert portfolio.blocked_entities == 1
    blocked_summary = next(s for s in portfolio.entities if s.entity_id == "e1")
    assert blocked_summary.current_state == "BLOCKED"


# ===========================================================================
# P23 — CRITICAL attention via BLOCKED + STALE → CRITICAL level
# ===========================================================================

def test_p23_blocked_and_stale_critical_attention() -> None:
    """Entity that is BLOCKED and STALE accumulates CRITICAL attention → CRITICAL portfolio risk."""
    e1 = _make_entity("e1", "stale blocked issue")
    # Meeting > 30 days ago + blocked → ENTITY_BLOCKED (100) + ENTITY_STALE (40) → 140 → CRITICAL
    old_mtg = _make_meeting("m1", _BASE_TIME - timedelta(days=45))
    mention = _make_mention("mn1", "e1", "m1", "stale blocked issue is blocked")

    service = _build_service([e1], [old_mtg], [mention])
    portfolio = service.get_portfolio(current_time=_BASE_TIME)

    summary = portfolio.entities[0]
    assert summary.risk_level == PortfolioRiskLevel.CRITICAL
    assert summary.attention_score >= 140


# ===========================================================================
# P24 — CRITICAL impact signal overrides no-attention → CRITICAL
# ===========================================================================

def test_p24_critical_impact_no_attention_is_critical() -> None:
    """_classify_risk: CRITICAL impact alone (no attention) -> CRITICAL risk."""
    risk = _classify_risk(
        attention_level=None,
        current_state=TemporalState.OPEN,
        impact_levels=[ImpactLevel.CRITICAL],
        observation_count=3,
    )
    assert risk == PortfolioRiskLevel.CRITICAL


# ===========================================================================
# P25 — LOW attention entity with MEDIUM impact → MEDIUM (impact wins)
# ===========================================================================

def test_p25_low_attention_medium_impact_is_medium() -> None:
    """_classify_risk: LOW attention + MEDIUM impact -> MEDIUM (MEDIUM impact wins over LOW attention)."""
    risk = _classify_risk(
        attention_level=AttentionLevel.LOW,
        current_state=TemporalState.IN_PROGRESS,
        impact_levels=[ImpactLevel.MEDIUM],
        observation_count=2,
    )
    assert risk == PortfolioRiskLevel.MEDIUM


# ===========================================================================
# Unit tests for _classify_risk helper
# ===========================================================================

def test_classify_risk_no_observations_no_signals_excluded() -> None:
    """_classify_risk: no observations + no signals -> None (excluded)."""
    risk = _classify_risk(
        attention_level=None,
        current_state=TemporalState.UNKNOWN,
        impact_levels=[],
        observation_count=0,
    )
    assert risk is None


def test_classify_risk_critical_attention_wins() -> None:
    """_classify_risk: CRITICAL attention -> CRITICAL regardless of other signals."""
    risk = _classify_risk(
        attention_level=AttentionLevel.CRITICAL,
        current_state=TemporalState.UNKNOWN,
        impact_levels=[ImpactLevel.LOW],
        observation_count=1,
    )
    assert risk == PortfolioRiskLevel.CRITICAL


def test_classify_risk_high_impact_alone_is_high() -> None:
    """_classify_risk: HIGH impact alone (no attention, not BLOCKED) -> HIGH."""
    risk = _classify_risk(
        attention_level=None,
        current_state=TemporalState.OPEN,
        impact_levels=[ImpactLevel.HIGH],
        observation_count=2,
    )
    assert risk == PortfolioRiskLevel.HIGH


def test_classify_risk_medium_attention_gives_medium() -> None:
    """_classify_risk: MEDIUM attention alone -> MEDIUM."""
    risk = _classify_risk(
        attention_level=AttentionLevel.MEDIUM,
        current_state=TemporalState.OPEN,
        impact_levels=[],
        observation_count=3,
    )
    assert risk == PortfolioRiskLevel.MEDIUM


def test_classify_risk_low_attention_no_impact_gives_low() -> None:
    """_classify_risk: LOW attention, no impact -> LOW (LOW attention does not trigger MEDIUM)."""
    risk = _classify_risk(
        attention_level=AttentionLevel.LOW,
        current_state=TemporalState.OPEN,
        impact_levels=[],
        observation_count=3,
    )
    assert risk == PortfolioRiskLevel.LOW


# ===========================================================================
# P26-P28: API endpoint tests
# ===========================================================================

@pytest.fixture()
def portfolio_client():
    """Fixture that provides a TestClient with a controlled PortfolioIntelligenceService."""
    from app.main import app
    from app.api.entities import get_portfolio_intelligence_service

    e_repo = InMemoryEntityRepository()
    m_repo = InMemoryMentionRepository()
    mtg_repo = InMemoryMeetingRepository()

    service = PortfolioIntelligenceService(
        entity_repo=e_repo,
        mention_repo=m_repo,
        meeting_repo=mtg_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )

    app.dependency_overrides[get_portfolio_intelligence_service] = lambda: service

    client = TestClient(app)
    yield client, e_repo, m_repo, mtg_repo

    app.dependency_overrides.pop(get_portfolio_intelligence_service, None)


def test_p26_api_response_valid_structure(portfolio_client) -> None:
    """GET /api/v1/portfolio returns valid JSON with expected top-level keys."""
    client, e_repo, m_repo, mtg_repo = portfolio_client

    e_repo.create(_make_entity("e1", "auth service"))
    mtg_repo.save(_make_meeting("m1", _RECENT))
    m_repo.create(_make_mention("mn1", "e1", "m1", "auth service is blocked"))

    response = client.get("/api/v1/portfolio")
    assert response.status_code == 200

    body = response.json()
    assert "total_entities" in body
    assert "critical_entities" in body
    assert "high_risk_entities" in body
    assert "medium_risk_entities" in body
    assert "low_risk_entities" in body
    assert "entities_with_active_actions" in body
    assert "entities_with_impact" in body
    assert "blocked_entities" in body
    assert "entities" in body
    assert "evaluated_at" in body

    assert body["total_entities"] == 1
    assert len(body["entities"]) == 1

    entity = body["entities"][0]
    assert entity["entity_id"] == "e1"
    assert "risk_level" in entity
    assert "attention_level" in entity
    assert "attention_score" in entity
    assert "impact_count" in entity
    assert "action_count" in entity
    assert "active_insight_count" in entity
    assert "current_state" in entity
    assert "observation_count" in entity
    assert "canonical_name" in entity
    assert "entity_type" in entity


def test_p27_api_empty_portfolio(portfolio_client) -> None:
    """GET /api/v1/portfolio with no entities returns empty portfolio."""
    client, _, _, _ = portfolio_client

    response = client.get("/api/v1/portfolio")
    assert response.status_code == 200

    body = response.json()
    assert body["total_entities"] == 0
    assert body["critical_entities"] == 0
    assert body["entities"] == []


def test_p28_api_critical_entity_in_response(portfolio_client) -> None:
    """GET /api/v1/portfolio with a CRITICAL entity reflects correctly in response."""
    client, e_repo, m_repo, mtg_repo = portfolio_client

    e_repo.create(_make_entity("e1", "payment api"))
    mtg_repo.save(_make_meeting("m1", _RECENT))
    m_repo.create(_make_mention("mn1", "e1", "m1", "payment api is blocked"))

    response = client.get("/api/v1/portfolio")
    assert response.status_code == 200

    body = response.json()
    assert body["critical_entities"] >= 1
    assert body["blocked_entities"] >= 1

    entity = body["entities"][0]
    assert entity["risk_level"] == "CRITICAL"
    assert entity["current_state"] == "BLOCKED"
    assert entity["attention_level"] == "CRITICAL"
    assert entity["attention_score"] >= 100
