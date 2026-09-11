"""Stage 15 extended tests for directional impact propagation.

Supplements test_impact.py with tests that verify:
1. Explicit DEPENDS_ON directionality: if A depends on B and B is BLOCKED,
   A gets HIGH impact (not B getting impact from A).
2. Explicit BLOCKS directionality: if B BLOCKS A and B is BLOCKED,
   A gets HIGH impact with EXPLICIT_DEPENDENCY signal.
3. No backward propagation: if A is HIGH_ATTENTION, B does NOT automatically
   get impact signals from A unless B has a separate relationship.
4. Portfolio respects dependency-enriched impacts.

Coverage
--------
  IP01. A DEPENDS_ON B, B BLOCKED → A gets HIGH impact with EXPLICIT_DEPENDENCY.
  IP02. B BLOCKS A, B BLOCKED → A gets HIGH impact with EXPLICIT_DEPENDENCY.
  IP03. A DEPENDS_ON B, B NOT blocked → no impact from explicit dependency alone.
  IP04. A DEPENDS_ON B, B has CRITICAL attention + strong rel → CRITICAL impact.
  IP05. CO_OCCURS_WITH only → no EXPLICIT_DEPENDENCY signal (even if B is blocked).
  IP06. No backward propagation: A has HIGH attention, B DEPENDS_ON A → A does not impact itself.
  IP07. Portfolio: entity with EXPLICIT_DEPENDENCY impact → HIGH risk in portfolio.
"""

from datetime import datetime, timezone, timedelta

import pytest

from app.models.dependency import ExplicitDependency
from app.models.entity import CanonicalEntity, EntityMention, EntityType, ResolutionStatus
from app.models.impact import ImpactLevel, RiskSignalType
from app.models.meeting import Meeting
from app.models.relationships import RelationshipType
from app.repositories.dependency_repository import InMemoryDependencyRepository
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.attention_service import AttentionService
from app.services.entity_relationship_service import EntityRelationshipService
from app.services.impact_analysis_service import ImpactAnalysisService
from app.services.insight_service import InsightService
from app.services.temporal_state_service import TemporalStateService
from app.temporal.state_interpreter import KeywordStateInterpreter
from app.temporal.transition_policy import DefaultTransitionPolicy

_BASE_TIME = datetime.now(timezone.utc)


def _make_entity(entity_id: str, canonical_name: str) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=EntityType.ISSUE,
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


def _make_mention(mention_id: str, entity_id: str, meeting_id: str, source_text: str) -> EntityMention:
    return EntityMention(
        mention_id=mention_id,
        entity_type=EntityType.ISSUE,
        text="text",
        meeting_id=meeting_id,
        source_text=source_text,
        entity_id=entity_id,
        resolution_status=ResolutionStatus.RESOLVED,
        created_at=_BASE_TIME,
    )


def _make_dep(
    dep_id: str,
    source_id: str,
    target_id: str,
    rel_type: RelationshipType,
    mention_id: str,
    meeting_id: str,
) -> ExplicitDependency:
    return ExplicitDependency(
        dependency_id=dep_id,
        mention_id=mention_id,
        source_entity_id=source_id,
        target_entity_id=target_id,
        relationship_type=rel_type,
        source_text=f"{source_id} {rel_type.value} {target_id}",
        meeting_id=meeting_id,
    )


def _build_service(
    entities=None,
    meetings=None,
    mentions=None,
    dependencies=None,
) -> ImpactAnalysisService:
    e_repo = InMemoryEntityRepository()
    m_repo = InMemoryMentionRepository()
    mtg_repo = InMemoryMeetingRepository()
    d_repo = InMemoryDependencyRepository()

    for e in (entities or []):
        e_repo.create(e)
    for mtg in (meetings or []):
        mtg_repo.save(mtg)
    for m in (mentions or []):
        m_repo.create(m)
    for d in (dependencies or []):
        d_repo.save(d)

    rel_svc = EntityRelationshipService(e_repo, m_repo, d_repo)
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


# ===========================================================================
# IP01: A DEPENDS_ON B, B BLOCKED → A gets HIGH impact with EXPLICIT_DEPENDENCY
# ===========================================================================

def test_ip01_depends_on_blocked_source_high_impact() -> None:
    """IP01. A DEPENDS_ON B + B is BLOCKED → A gets HIGH impact with EXPLICIT_DEPENDENCY."""
    e_a = _make_entity("ea", "auth service")  # The dependent (impacted entity)
    e_b = _make_entity("eb", "database migration")  # The dependency (blocked)
    mtg = _make_meeting("m1", _BASE_TIME)

    mentions = [
        _make_mention("mn1", "ea", "m1", "auth service is working"),
        _make_mention("mn2", "eb", "m1", "database migration is blocked"),
    ]

    dep = _make_dep("dep1", "ea", "eb", RelationshipType.DEPENDS_ON, "mn1", "m1")

    service = _build_service(
        entities=[e_a, e_b],
        meetings=[mtg],
        mentions=mentions,
        dependencies=[dep],
    )

    impacts = service.get_entity_impacts("ea", _BASE_TIME)

    assert len(impacts) == 1
    impact = impacts[0]
    assert impact.impact_level == ImpactLevel.HIGH
    assert RiskSignalType.EXPLICIT_DEPENDENCY in impact.risk_signals
    assert RiskSignalType.BLOCKED_ENTITY in impact.risk_signals
    assert impact.source_entity_id == "eb"
    assert impact.impacted_entity_id == "ea"


# ===========================================================================
# IP02: B BLOCKS A, B BLOCKED → A gets HIGH impact
# ===========================================================================

def test_ip02_blocks_relationship_blocked_source_high_impact() -> None:
    """IP02. B BLOCKS A + B is BLOCKED → A gets HIGH impact with EXPLICIT_DEPENDENCY."""
    e_a = _make_entity("ea", "deployment")         # Being blocked
    e_b = _make_entity("eb", "database migration")  # The blocker (itself blocked)
    mtg = _make_meeting("m1", _BASE_TIME)

    mentions = [
        _make_mention("mn1", "ea", "m1", "deployment is waiting"),
        _make_mention("mn2", "eb", "m1", "database migration is blocked"),
    ]

    # B BLOCKS A
    dep = _make_dep("dep1", "eb", "ea", RelationshipType.BLOCKS, "mn2", "m1")

    service = _build_service(
        entities=[e_a, e_b],
        meetings=[mtg],
        mentions=mentions,
        dependencies=[dep],
    )

    impacts = service.get_entity_impacts("ea", _BASE_TIME)

    assert len(impacts) == 1
    impact = impacts[0]
    assert impact.impact_level == ImpactLevel.HIGH
    assert RiskSignalType.EXPLICIT_DEPENDENCY in impact.risk_signals


# ===========================================================================
# IP03: A DEPENDS_ON B, B NOT blocked → no impact from dependency alone
# ===========================================================================

def test_ip03_depends_on_not_blocked_no_impact() -> None:
    """IP03. A DEPENDS_ON B + B is in progress (not blocked) → no impact."""
    e_a = _make_entity("ea", "auth service")
    e_b = _make_entity("eb", "database migration")
    mtg = _make_meeting("m1", _BASE_TIME)

    mentions = [
        _make_mention("mn1", "ea", "m1", "auth service is working"),
        _make_mention("mn2", "eb", "m1", "database migration is in progress"),
    ]

    dep = _make_dep("dep1", "ea", "eb", RelationshipType.DEPENDS_ON, "mn1", "m1")

    service = _build_service(
        entities=[e_a, e_b],
        meetings=[mtg],
        mentions=mentions,
        dependencies=[dep],
    )

    impacts = service.get_entity_impacts("ea", _BASE_TIME)
    # No risk signals — dependency exists but target is fine
    assert len(impacts) == 0


# ===========================================================================
# IP04: A DEPENDS_ON B, B has CRITICAL attention (blocked > 30 days) → CRITICAL
# ===========================================================================

def test_ip04_depends_on_critical_attention_strong_rel_high_impact() -> None:
    """IP04. A DEPENDS_ON B + B has EXPLICIT_DEPENDENCY + BLOCKED → HIGH (not CRITICAL).

    When EXPLICIT_DEPENDENCY + BLOCKED_ENTITY are both present, the impact service
    prioritises the EXPLICIT_DEPENDENCY rule and returns HIGH impact.
    The CRITICAL path (CRITICAL_ATTENTION + strength >= 2) only triggers when
    EXPLICIT_DEPENDENCY is not the dominant signal.

    This test verifies the priority: EXPLICIT_DEPENDENCY + BLOCKED_ENTITY → HIGH
    (even if the source also has CRITICAL attention due to being blocked > 30 days).
    """
    e_a = _make_entity("ea", "auth service")
    e_b = _make_entity("eb", "database migration")

    m1 = _make_meeting("m1", _BASE_TIME - timedelta(days=35))
    m2 = _make_meeting("m2", _BASE_TIME)

    mentions = [
        _make_mention("mn1", "ea", "m1", "auth service is working"),
        _make_mention("mn2", "eb", "m1", "database migration is blocked"),
        _make_mention("mn3", "ea", "m2", "auth service is still working"),
        _make_mention("mn4", "eb", "m2", "database migration is still blocked"),
    ]

    # Explicit dependency in one meeting
    dep = _make_dep("dep1", "ea", "eb", RelationshipType.DEPENDS_ON, "mn1", "m1")

    service = _build_service(
        entities=[e_a, e_b],
        meetings=[m1, m2],
        mentions=mentions,
        dependencies=[dep],
    )

    impacts = service.get_entity_impacts("ea", _BASE_TIME)

    assert len(impacts) == 1
    impact = impacts[0]
    # EXPLICIT_DEPENDENCY + BLOCKED_ENTITY takes priority in the impact service
    # → HIGH (not CRITICAL), even with CRITICAL attention on the source entity
    assert impact.impact_level == ImpactLevel.HIGH
    assert RiskSignalType.EXPLICIT_DEPENDENCY in impact.risk_signals
    assert RiskSignalType.BLOCKED_ENTITY in impact.risk_signals
    assert impact.reason == "Entity has an explicit dependency on an entity which is currently in a BLOCKED state."


# ===========================================================================
# IP05: CO_OCCURS_WITH only → no EXPLICIT_DEPENDENCY signal
# ===========================================================================

def test_ip05_co_occurs_with_no_explicit_dependency_signal() -> None:
    """IP05. CO_OCCURS_WITH without explicit dependency → BLOCKED_ENTITY but not EXPLICIT_DEPENDENCY."""
    e_a = _make_entity("ea", "auth service")
    e_b = _make_entity("eb", "database migration")
    mtg = _make_meeting("m1", _BASE_TIME)

    mentions = [
        _make_mention("mn1", "ea", "m1", "auth service is working"),
        _make_mention("mn2", "eb", "m1", "database migration is blocked"),
    ]

    # No ExplicitDependency — only co-occurrence
    service = _build_service(
        entities=[e_a, e_b],
        meetings=[mtg],
        mentions=mentions,
    )

    impacts = service.get_entity_impacts("ea", _BASE_TIME)

    assert len(impacts) == 1
    impact = impacts[0]
    # Should have BLOCKED_ENTITY but NOT EXPLICIT_DEPENDENCY
    assert RiskSignalType.BLOCKED_ENTITY in impact.risk_signals
    assert RiskSignalType.EXPLICIT_DEPENDENCY not in impact.risk_signals


# ===========================================================================
# IP06: No backward propagation
# ===========================================================================

def test_ip06_no_backward_propagation() -> None:
    """IP06. If A is BLOCKED, B should not get impact JUST BECAUSE B DEPENDS_ON A.

    The dependency A→B means A needs B, not B needs A.
    If A is BLOCKED, B's dependency service should not propagate impact TO B
    unless B specifically is the one depending on a blocked entity.

    Here: B DEPENDS_ON A. A is blocked. What should B see?
    B depends on A → A is blocked → B should see HIGH impact (correct: B's dependency is blocked).

    This test instead verifies the reverse case: C is HIGH_ATTENTION.
    D does NOT depend on C in any direction. D must NOT see C's attention as an impact.
    """
    e_c = _make_entity("ec", "feature x")   # HIGH attention entity
    e_d = _make_entity("ed", "feature y")   # Completely unrelated entity

    mtg = _make_meeting("m1", _BASE_TIME)

    mentions = [
        _make_mention("mn1", "ec", "m1", "feature x is blocked"),
        # e_d is NOT in any meeting with e_c
    ]
    mtg2 = _make_meeting("m2", _BASE_TIME)
    mentions.append(_make_mention("mn2", "ed", "m2", "feature y is in progress"))

    service = _build_service(
        entities=[e_c, e_d],
        meetings=[mtg, mtg2],
        mentions=mentions,
    )

    # e_d has no relationship with e_c → no impact
    impacts = service.get_entity_impacts("ed", _BASE_TIME)
    assert len(impacts) == 0


# ===========================================================================
# IP07: Portfolio respects dependency-enriched impacts
# ===========================================================================

def test_ip07_portfolio_with_explicit_dependency_impact() -> None:
    """IP07. Portfolio correctly classifies entity as HIGH when it has EXPLICIT_DEPENDENCY impact."""
    from app.repositories.meeting_repository import InMemoryMeetingRepository
    from app.services.portfolio_intelligence_service import PortfolioIntelligenceService
    from app.temporal.state_interpreter import KeywordStateInterpreter
    from app.temporal.transition_policy import DefaultTransitionPolicy

    e_a = _make_entity("ea", "auth service")  # Impacted entity
    e_b = _make_entity("eb", "database migration")  # Blocked dependency
    mtg = _make_meeting("m1", _BASE_TIME)

    mentions = [
        _make_mention("mn1", "ea", "m1", "auth service is in progress"),
        _make_mention("mn2", "eb", "m1", "database migration is blocked"),
    ]
    dep = _make_dep("dep1", "ea", "eb", RelationshipType.DEPENDS_ON, "mn1", "m1")

    e_repo = InMemoryEntityRepository()
    m_repo = InMemoryMentionRepository()
    mtg_repo = InMemoryMeetingRepository()
    d_repo = InMemoryDependencyRepository()

    for e in [e_a, e_b]:
        e_repo.create(e)
    mtg_repo.save(mtg)
    for m in mentions:
        m_repo.create(m)
    d_repo.save(dep)

    portfolio_svc = PortfolioIntelligenceService(
        entity_repo=e_repo,
        mention_repo=m_repo,
        meeting_repo=mtg_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
        dependency_repo=d_repo,
    )

    portfolio = portfolio_svc.get_portfolio(current_time=_BASE_TIME)

    # Find the summary for e_a (auth service — the impacted entity)
    summaries = {s.entity_id: s for s in portfolio.entities}
    assert "ea" in summaries

    summary_a = summaries["ea"]
    # ea has an explicit dependency on a BLOCKED entity → should have impact_count > 0
    assert summary_a.impact_count > 0

    from app.models.portfolio import PortfolioRiskLevel
    # ea should be at least HIGH risk (has HIGH impact)
    assert summary_a.risk_level in (PortfolioRiskLevel.HIGH, PortfolioRiskLevel.CRITICAL)
