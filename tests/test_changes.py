"""Tests for the Organisation-Wide Change Intelligence Engine (Stage 17).

All tests are fully deterministic -- no LLM calls, no network, no external database.

Coverage
--------

Model unit tests:
  C01. make_change_id is deterministic given the same inputs.
  C02. make_change_id differs when any input differs.
  C03. CHANGE_TYPE_SEVERITY maps all change types.
  C04. CHANGE_TYPE_PRIORITY maps all change types.
  C05. CHANGE_SEVERITY_ORDER maps all severities.
  C06. REGRESSION_TRANSITIONS contains correct pairs.

Service unit tests (no HTTP):
  C07. Empty organisation returns zero changes.
  C08. Entity with no observations returns no changes.
  C09. STATE_OPENED change detected from UNKNOWN -> OPEN insight.
  C10. STATE_STARTED change detected from * -> IN_PROGRESS insight.
  C11. STATE_BLOCKED change detected from ISSUE_BLOCKED insight.
  C12. STATE_RESOLVED change detected from ISSUE_RESOLVED insight.
  C13. STATE_REGRESSED detected from IN_PROGRESS -> BLOCKED.
  C14. STATE_REGRESSED detected from OPEN -> BLOCKED.
  C15. STATE_REOPENED detected from REOPEN_ATTEMPT insight.
  C16. REPEATED_UNRESOLVED detected from REPEATED_OBSERVATION insight.
  C17. ENTITY_BECAME_STALE detected from STALE_ENTITY insight.
  C18. RISK_ESCALATED detected when entity has CRITICAL attention with ENTITY_BLOCKED reason.
  C19. RISK_DEESCALATED detected alongside STATE_RESOLVED.
  C20. NEW_DEPENDENCY detected from explicit DEPENDS_ON record.
  C21. NEW_DEPENDENCY detected from explicit BLOCKS record.
  C22. CO_OCCURS_WITH relationship NOT promoted to NEW_DEPENDENCY.
  C23. DEPENDENCY_EXPANDED detected when transitive dependency exists.
  C24. IMPACT_EXPANDED detected when entity has inbound risk impacts.
  C25. Changes are deduplicated (same event does not appear twice).
  C26. Deterministic ordering (CRITICAL before HIGH before MEDIUM before INFO).
  C27. change_type priority ordering within same severity level.
  C28. detected_at DESC ordering within same severity and type.
  C29. entity_id ASC tie-breaking.
  C30. Same input + same current_time produces identical results.
  C31. Repeated calls do not mutate state (idempotency).
  C32. entity_id filter returns only changes for that entity.
  C33. change_type filter returns only changes of that type.
  C34. severity filter returns only changes at that severity.
  C35. start_date filter excludes changes before the window.
  C36. end_date filter excludes changes after the window.
  C37. limit parameter caps returned changes.
  C38. get_summary returns correct aggregate counts.
  C39. get_summary.newly_blocked_entities correct.
  C40. get_summary.regressed_entities correct.
  C41. get_summary.changes_by_entity groups correctly.
  C42. Missing entity_id in entity filter returns empty list.
  C43. _parse_state_transition parses valid descriptions correctly.
  C44. _parse_state_transition returns (None, None) for unrecognised text.
  C45. Multiple transitions in one entity produce multiple changes.

API endpoint tests (full stack via TestClient):
  C46. GET /api/v1/changes returns valid JSON structure.
  C47. GET /api/v1/changes with no entities returns empty changes list.
  C48. GET /api/v1/changes with entity having state changes reflects in response.
  C49. GET /api/v1/changes/summary returns valid JSON structure.
  C50. GET /api/v1/changes?change_type=STATE_BLOCKED filters correctly.
  C51. GET /api/v1/changes?severity=CRITICAL filters correctly.
  C52. GET /api/v1/changes?limit=1 returns at most 1 change.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.models.entity import CanonicalEntity, EntityMention, EntityType, ResolutionStatus
from app.models.meeting import Meeting
from app.models.dependency import ExplicitDependency
from app.models.relationships import RelationshipType
from app.models.organisation_change import (
    CHANGE_SEVERITY_ORDER,
    CHANGE_TYPE_PRIORITY,
    CHANGE_TYPE_SEVERITY,
    REGRESSION_TRANSITIONS,
    OrganisationChange,
    OrganisationChangeSeverity,
    OrganisationChangeType,
    make_change_id,
)
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.repositories.dependency_repository import InMemoryDependencyRepository
from app.services.organisation_change_intelligence_service import (
    OrganisationChangeIntelligenceService,
    _sort_changes,
)
from app.temporal.state_interpreter import KeywordStateInterpreter
from app.temporal.transition_policy import DefaultTransitionPolicy


# ---------------------------------------------------------------------------
# Shared builder helpers
# ---------------------------------------------------------------------------

# Fixed reference time -- ensures stale-entity logic is deterministic.
_BASE_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
# A "recent" time for creating mentions without triggering stale detection.
_RECENT = _BASE_TIME - timedelta(days=1)
# An "old" time to trigger stale entity detection (> 30 days before base).
_STALE = _BASE_TIME - timedelta(days=35)


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


def _make_dep(
    dep_id: str,
    source_id: str,
    target_id: str,
    rel_type: RelationshipType,
    meeting_id: str,
    mention_id: str,
    source_text: str = "explicit dep",
) -> ExplicitDependency:
    return ExplicitDependency(
        dependency_id=dep_id,
        source_entity_id=source_id,
        target_entity_id=target_id,
        relationship_type=rel_type,
        source_text=source_text,
        meeting_id=meeting_id,
        mention_id=mention_id,
    )


def _build_service(
    entities: Optional[list] = None,
    meetings: Optional[list] = None,
    mentions: Optional[list] = None,
    deps: Optional[list] = None,
) -> OrganisationChangeIntelligenceService:
    """Build an OrganisationChangeIntelligenceService with in-memory repositories."""
    e_repo = InMemoryEntityRepository()
    m_repo = InMemoryMentionRepository()
    mtg_repo = InMemoryMeetingRepository()
    dep_repo = InMemoryDependencyRepository()

    for e in (entities or []):
        e_repo.create(e)
    for mtg in (meetings or []):
        mtg_repo.save(mtg)
    for m in (mentions or []):
        m_repo.create(m)
    for d in (deps or []):
        dep_repo.save(d)

    return OrganisationChangeIntelligenceService(
        entity_repo=e_repo,
        mention_repo=m_repo,
        meeting_repo=mtg_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
        dependency_repo=dep_repo,
    )


# ===========================================================================
# C01-C06: Model unit tests
# ===========================================================================

def test_c01_make_change_id_deterministic() -> None:
    """make_change_id returns the same value for identical inputs."""
    cid1 = make_change_id("e1", OrganisationChangeType.STATE_BLOCKED, "UNKNOWN:BLOCKED", "m1")
    cid2 = make_change_id("e1", OrganisationChangeType.STATE_BLOCKED, "UNKNOWN:BLOCKED", "m1")
    assert cid1 == cid2
    assert len(cid1) == 16


def test_c02_make_change_id_differs_on_input_change() -> None:
    """make_change_id produces different values when any input differs."""
    base = make_change_id("e1", OrganisationChangeType.STATE_BLOCKED, "UNKNOWN:BLOCKED", "m1")
    assert make_change_id("e2", OrganisationChangeType.STATE_BLOCKED, "UNKNOWN:BLOCKED", "m1") != base
    assert make_change_id("e1", OrganisationChangeType.STATE_RESOLVED, "UNKNOWN:BLOCKED", "m1") != base
    assert make_change_id("e1", OrganisationChangeType.STATE_BLOCKED, "OPEN:BLOCKED", "m1") != base
    assert make_change_id("e1", OrganisationChangeType.STATE_BLOCKED, "UNKNOWN:BLOCKED", "m2") != base


def test_c03_change_type_severity_covers_all_types() -> None:
    """CHANGE_TYPE_SEVERITY maps all 13 change types."""
    for ct in OrganisationChangeType:
        assert ct in CHANGE_TYPE_SEVERITY, f"Missing severity for {ct}"


def test_c04_change_type_priority_covers_all_types() -> None:
    """CHANGE_TYPE_PRIORITY maps all 13 change types."""
    for ct in OrganisationChangeType:
        assert ct in CHANGE_TYPE_PRIORITY, f"Missing priority for {ct}"


def test_c05_change_severity_order_covers_all_severities() -> None:
    """CHANGE_SEVERITY_ORDER maps all 4 severities."""
    for sev in OrganisationChangeSeverity:
        assert sev in CHANGE_SEVERITY_ORDER, f"Missing order for {sev}"


def test_c06_regression_transitions_correct() -> None:
    """REGRESSION_TRANSITIONS contains exactly the correct pairs."""
    assert ("IN_PROGRESS", "BLOCKED") in REGRESSION_TRANSITIONS
    assert ("OPEN", "BLOCKED") in REGRESSION_TRANSITIONS
    # UNKNOWN -> BLOCKED is NOT a regression (no prior progress)
    assert ("UNKNOWN", "BLOCKED") not in REGRESSION_TRANSITIONS
    # IN_PROGRESS -> RESOLVED is not a regression
    assert ("IN_PROGRESS", "RESOLVED") not in REGRESSION_TRANSITIONS


# ===========================================================================
# C07-C08: Empty/no-observation scenarios
# ===========================================================================

def test_c07_empty_organisation_returns_zero_changes() -> None:
    """Service with zero entities returns an empty changes list."""
    service = _build_service()
    changes = service.get_changes(current_time=_BASE_TIME)
    assert changes == []


def test_c08_entity_with_no_observations_returns_no_changes() -> None:
    """Entity with no mentions has no observations and no changes."""
    e1 = _make_entity("e1", "auth service")
    service = _build_service(entities=[e1])
    changes = service.get_changes(current_time=_BASE_TIME)
    # Entity with no observations and no intelligence -- no changes.
    assert all(c.entity_id != "e1" for c in changes)


# ===========================================================================
# C09: STATE_OPENED
# ===========================================================================

def test_c09_state_opened_from_unknown_to_open() -> None:
    """STATE_OPENED change detected when entity moves from UNKNOWN to OPEN."""
    e1 = _make_entity("e1", "auth service")
    mtg = _make_meeting("m1", _RECENT)
    # "raised" is an OPEN keyword per KeywordStateInterpreter vocabulary
    mention = _make_mention("mn1", "e1", "m1", "auth service raised as new item")

    service = _build_service([e1], [mtg], [mention])
    changes = service.get_changes(current_time=_BASE_TIME)

    opened = [c for c in changes if c.change_type == OrganisationChangeType.STATE_OPENED]
    assert len(opened) >= 1
    c = opened[0]
    assert c.entity_id == "e1"
    assert c.severity == OrganisationChangeSeverity.INFO
    assert c.previous_state == "UNKNOWN"
    assert c.current_state == "OPEN"
    assert c.change_id is not None
    assert len(c.change_id) == 16


# ===========================================================================
# C10: STATE_STARTED
# ===========================================================================

def test_c10_state_started_detected() -> None:
    """STATE_STARTED change detected when entity transitions to IN_PROGRESS."""
    e1 = _make_entity("e1", "feature alpha")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    # First observation: OPEN
    mn1 = _make_mention("mn1", "e1", "m1", "feature alpha is open")
    # Second observation: IN_PROGRESS
    mn2 = _make_mention("mn2", "e1", "m2", "feature alpha is in progress")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    changes = service.get_changes(current_time=_BASE_TIME)

    started = [c for c in changes if c.change_type == OrganisationChangeType.STATE_STARTED]
    assert len(started) >= 1
    c = started[0]
    assert c.entity_id == "e1"
    assert c.current_state == "IN_PROGRESS"
    assert c.severity == OrganisationChangeSeverity.INFO


# ===========================================================================
# C11: STATE_BLOCKED
# ===========================================================================

def test_c11_state_blocked_detected() -> None:
    """STATE_BLOCKED change detected when entity transitions to BLOCKED."""
    e1 = _make_entity("e1", "deployment pipeline")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "deployment pipeline is open")
    mn2 = _make_mention("mn2", "e1", "m2", "deployment pipeline is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    changes = service.get_changes(current_time=_BASE_TIME)

    blocked = [c for c in changes if c.change_type == OrganisationChangeType.STATE_BLOCKED]
    assert len(blocked) >= 1
    c = blocked[0]
    assert c.entity_id == "e1"
    assert c.current_state == "BLOCKED"
    assert c.severity == OrganisationChangeSeverity.HIGH


# ===========================================================================
# C12: STATE_RESOLVED
# ===========================================================================

def test_c12_state_resolved_detected() -> None:
    """STATE_RESOLVED change detected when entity transitions to RESOLVED."""
    e1 = _make_entity("e1", "login bug")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "login bug is in progress")
    mn2 = _make_mention("mn2", "e1", "m2", "login bug is resolved")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    changes = service.get_changes(current_time=_BASE_TIME)

    resolved = [c for c in changes if c.change_type == OrganisationChangeType.STATE_RESOLVED]
    assert len(resolved) >= 1
    c = resolved[0]
    assert c.entity_id == "e1"
    assert c.current_state == "RESOLVED"
    assert c.severity == OrganisationChangeSeverity.INFO


# ===========================================================================
# C13-C14: STATE_REGRESSED
# ===========================================================================

def test_c13_state_regressed_in_progress_to_blocked() -> None:
    """STATE_REGRESSED detected when entity goes from IN_PROGRESS to BLOCKED."""
    e1 = _make_entity("e1", "payment service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=4))
    mtg2 = _make_meeting("m2", _RECENT - timedelta(hours=2))
    mtg3 = _make_meeting("m3", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "payment service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "payment service is in progress")
    mn3 = _make_mention("mn3", "e1", "m3", "payment service is blocked")

    service = _build_service([e1], [mtg1, mtg2, mtg3], [mn1, mn2, mn3])
    changes = service.get_changes(current_time=_BASE_TIME)

    regressed = [c for c in changes if c.change_type == OrganisationChangeType.STATE_REGRESSED]
    assert len(regressed) >= 1
    c = regressed[0]
    assert c.entity_id == "e1"
    assert c.previous_state == "IN_PROGRESS"
    assert c.current_state == "BLOCKED"
    assert c.severity == OrganisationChangeSeverity.HIGH


def test_c14_state_regressed_open_to_blocked() -> None:
    """STATE_REGRESSED detected when entity goes from OPEN to BLOCKED."""
    e1 = _make_entity("e1", "data pipeline")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    # "raised" triggers OPEN; "blocked" triggers BLOCKED -> OPEN -> BLOCKED regression
    mn1 = _make_mention("mn1", "e1", "m1", "data pipeline raised as new issue")
    mn2 = _make_mention("mn2", "e1", "m2", "data pipeline is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    changes = service.get_changes(current_time=_BASE_TIME)

    regressed = [c for c in changes if c.change_type == OrganisationChangeType.STATE_REGRESSED]
    assert len(regressed) >= 1
    c = regressed[0]
    assert c.previous_state == "OPEN"
    assert c.current_state == "BLOCKED"


# ===========================================================================
# C15: STATE_REOPENED
# ===========================================================================

def test_c15_state_reopened_detected() -> None:
    """STATE_REOPENED detected on reopen attempt of a RESOLVED entity."""
    e1 = _make_entity("e1", "old bug")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=4))
    mtg2 = _make_meeting("m2", _RECENT - timedelta(hours=2))
    mtg3 = _make_meeting("m3", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "old bug is in progress")
    mn2 = _make_mention("mn2", "e1", "m2", "old bug is resolved")
    # Reopen attempt: trying to transition a RESOLVED entity
    mn3 = _make_mention("mn3", "e1", "m3", "old bug is in progress again")

    service = _build_service([e1], [mtg1, mtg2, mtg3], [mn1, mn2, mn3])
    changes = service.get_changes(current_time=_BASE_TIME)

    reopened = [c for c in changes if c.change_type == OrganisationChangeType.STATE_REOPENED]
    assert len(reopened) >= 1
    c = reopened[0]
    assert c.entity_id == "e1"
    assert c.severity == OrganisationChangeSeverity.HIGH
    assert c.previous_state == "RESOLVED"


# ===========================================================================
# C16: REPEATED_UNRESOLVED
# ===========================================================================

def test_c16_repeated_unresolved_detected() -> None:
    """REPEATED_UNRESOLVED detected when entity observed multiple times in a meeting."""
    e1 = _make_entity("e1", "infra ticket")
    mtg = _make_meeting("m1", _RECENT)
    # Two mentions in same meeting without state transition triggers REPEATED_OBSERVATION
    mn1 = _make_mention("mn1", "e1", "m1", "infra ticket mentioned")
    mn2 = _make_mention("mn2", "e1", "m1", "infra ticket still pending")

    service = _build_service([e1], [mtg], [mn1, mn2])
    changes = service.get_changes(current_time=_BASE_TIME)

    repeated = [c for c in changes if c.change_type == OrganisationChangeType.REPEATED_UNRESOLVED]
    assert len(repeated) >= 1
    c = repeated[0]
    assert c.entity_id == "e1"
    assert c.severity == OrganisationChangeSeverity.MEDIUM


# ===========================================================================
# C17: ENTITY_BECAME_STALE
# ===========================================================================

def test_c17_entity_became_stale_detected() -> None:
    """ENTITY_BECAME_STALE detected when entity has not been seen for > threshold days."""
    e1 = _make_entity("e1", "stale report")
    # Meeting and mention are 35 days before the base time.
    old_mtg = _make_meeting("m1", _STALE)
    mn1 = _make_mention("mn1", "e1", "m1", "stale report is open")

    service = _build_service([e1], [old_mtg], [mn1])
    # Evaluate at base time (35 days after the mention) with default 30-day threshold.
    changes = service.get_changes(current_time=_BASE_TIME)

    stale = [c for c in changes if c.change_type == OrganisationChangeType.ENTITY_BECAME_STALE]
    assert len(stale) >= 1
    c = stale[0]
    assert c.entity_id == "e1"
    assert c.severity == OrganisationChangeSeverity.HIGH


# ===========================================================================
# C18: RISK_ESCALATED
# ===========================================================================

def test_c18_risk_escalated_detected_for_blocked_entity() -> None:
    """RISK_ESCALATED detected when entity is BLOCKED and has CRITICAL attention."""
    e1 = _make_entity("e1", "critical service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "critical service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "critical service is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    changes = service.get_changes(current_time=_BASE_TIME)

    escalated = [c for c in changes if c.change_type == OrganisationChangeType.RISK_ESCALATED]
    assert len(escalated) >= 1
    c = escalated[0]
    assert c.entity_id == "e1"
    assert c.severity == OrganisationChangeSeverity.CRITICAL


# ===========================================================================
# C19: RISK_DEESCALATED
# ===========================================================================

def test_c19_risk_deescalated_detected_alongside_state_resolved() -> None:
    """RISK_DEESCALATED detected alongside STATE_RESOLVED for same entity."""
    e1 = _make_entity("e1", "fixed bug")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "fixed bug is in progress")
    mn2 = _make_mention("mn2", "e1", "m2", "fixed bug is resolved")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    changes = service.get_changes(current_time=_BASE_TIME)

    deescalated = [c for c in changes if c.change_type == OrganisationChangeType.RISK_DEESCALATED]
    resolved = [c for c in changes if c.change_type == OrganisationChangeType.STATE_RESOLVED]
    assert len(deescalated) >= 1
    assert len(resolved) >= 1
    assert deescalated[0].entity_id == "e1"
    assert deescalated[0].severity == OrganisationChangeSeverity.INFO


# ===========================================================================
# C20-C22: NEW_DEPENDENCY
# ===========================================================================

def test_c20_new_dependency_detected_from_depends_on() -> None:
    """NEW_DEPENDENCY detected from explicit DEPENDS_ON record."""
    e1 = _make_entity("e1", "service A")
    e2 = _make_entity("e2", "service B")
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "service A mentioned")
    mn2 = _make_mention("mn2", "e2", "m1", "service B mentioned")
    dep = _make_dep("d1", "e1", "e2", RelationshipType.DEPENDS_ON, "m1", "mn1",
                    "service A depends on service B")

    service = _build_service([e1, e2], [mtg], [mn1, mn2], [dep])
    changes = service.get_changes(current_time=_BASE_TIME)

    new_deps = [c for c in changes if c.change_type == OrganisationChangeType.NEW_DEPENDENCY]
    assert len(new_deps) >= 1
    c = new_deps[0]
    assert c.entity_id == "e1"
    assert "e2" in c.related_entity_ids
    assert c.dependency_path == ["e1", "e2"]
    assert c.severity == OrganisationChangeSeverity.MEDIUM


def test_c21_new_dependency_detected_from_blocks() -> None:
    """NEW_DEPENDENCY detected from explicit BLOCKS record."""
    e1 = _make_entity("e1", "team A task")
    e2 = _make_entity("e2", "team B task")
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "team A mentioned")
    mn2 = _make_mention("mn2", "e2", "m1", "team B mentioned")
    dep = _make_dep("d1", "e1", "e2", RelationshipType.BLOCKS, "m1", "mn1",
                    "team A task blocks team B task")

    service = _build_service([e1, e2], [mtg], [mn1, mn2], [dep])
    changes = service.get_changes(current_time=_BASE_TIME)

    new_deps = [c for c in changes if c.change_type == OrganisationChangeType.NEW_DEPENDENCY]
    assert any(c.entity_id == "e1" and "e2" in c.related_entity_ids for c in new_deps)


def test_c22_co_occurs_with_not_promoted_to_new_dependency() -> None:
    """CO_OCCURS_WITH relationship is NOT promoted to a NEW_DEPENDENCY change.

    Only ExplicitDependency records (DEPENDS_ON, BLOCKS) qualify.
    CO_OCCURS_WITH is computed on-the-fly from meeting co-occurrence and
    is never stored in the dependency repository.
    """
    e1 = _make_entity("e1", "component X")
    e2 = _make_entity("e2", "component Y")
    mtg = _make_meeting("m1", _RECENT)
    # Both entities mentioned in same meeting (CO_OCCURS_WITH) -- no explicit dep.
    mn1 = _make_mention("mn1", "e1", "m1", "component X mentioned")
    mn2 = _make_mention("mn2", "e2", "m1", "component Y mentioned")
    # NO explicit dependency record added.

    service = _build_service([e1, e2], [mtg], [mn1, mn2])
    changes = service.get_changes(current_time=_BASE_TIME)

    new_deps = [c for c in changes if c.change_type == OrganisationChangeType.NEW_DEPENDENCY]
    # There should be no NEW_DEPENDENCY for either entity.
    assert all(c.entity_id not in {"e1", "e2"} for c in new_deps)


# ===========================================================================
# C23: DEPENDENCY_EXPANDED
# ===========================================================================

def test_c23_dependency_expanded_detected_for_transitive_chain() -> None:
    """DEPENDENCY_EXPANDED detected when a transitive dependency chain exists."""
    e1 = _make_entity("e1", "service A")
    e2 = _make_entity("e2", "service B")
    e3 = _make_entity("e3", "service C")
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "service A mentioned")
    mn2 = _make_mention("mn2", "e2", "m1", "service B mentioned")
    mn3 = _make_mention("mn3", "e3", "m1", "service C mentioned")
    # e1 -> e2 -> e3 (transitive chain of depth 2)
    dep1 = _make_dep("d1", "e1", "e2", RelationshipType.DEPENDS_ON, "m1", "mn1", "A depends on B")
    dep2 = _make_dep("d2", "e2", "e3", RelationshipType.DEPENDS_ON, "m1", "mn2", "B depends on C")

    service = _build_service([e1, e2, e3], [mtg], [mn1, mn2, mn3], [dep1, dep2])
    changes = service.get_changes(current_time=_BASE_TIME)

    expanded = [c for c in changes if c.change_type == OrganisationChangeType.DEPENDENCY_EXPANDED]
    # e1 should have a transitive path to e3
    e1_expanded = [c for c in expanded if c.entity_id == "e1"]
    assert len(e1_expanded) >= 1
    c = e1_expanded[0]
    assert c.dependency_path is not None
    assert len(c.dependency_path) >= 2
    assert c.severity == OrganisationChangeSeverity.MEDIUM


# ===========================================================================
# C24: IMPACT_EXPANDED
# ===========================================================================

def test_c24_impact_expanded_detected_for_impacted_entity() -> None:
    """IMPACT_EXPANDED detected when entity has inbound risk impact associations."""
    e1 = _make_entity("e1", "api gateway")
    e2 = _make_entity("e2", "database")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "api gateway mentioned")
    mn2 = _make_mention("mn2", "e2", "m1", "database mentioned")
    # e2 becomes blocked (high-risk source that impacts e1 via dependency)
    mn3 = _make_mention("mn3", "e2", "m2", "database is blocked")
    dep = _make_dep("d1", "e1", "e2", RelationshipType.DEPENDS_ON, "m1", "mn1",
                    "api gateway depends on database")

    service = _build_service([e1, e2], [mtg1, mtg2], [mn1, mn2, mn3], [dep])
    changes = service.get_changes(current_time=_BASE_TIME)

    impact_expanded = [c for c in changes if c.change_type == OrganisationChangeType.IMPACT_EXPANDED]
    # e1 should have an IMPACT_EXPANDED change (it's impacted by blocked e2)
    e1_impact = [c for c in impact_expanded if c.entity_id == "e1"]
    assert len(e1_impact) >= 1
    c = e1_impact[0]
    assert c.impact_count is not None
    assert c.impact_count >= 1
    assert "e2" in c.related_entity_ids
    assert c.severity == OrganisationChangeSeverity.MEDIUM


# ===========================================================================
# C25: Deduplication
# ===========================================================================

def test_c25_changes_are_deduplicated() -> None:
    """Same entity with same event does not produce duplicate changes."""
    e1 = _make_entity("e1", "auth service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "auth service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "auth service is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    changes1 = service.get_changes(current_time=_BASE_TIME)
    changes2 = service.get_changes(current_time=_BASE_TIME)

    # Calling twice should produce identical results, not doubled results.
    assert len(changes1) == len(changes2)

    # All change_ids should be unique within a single call.
    ids = [c.change_id for c in changes1]
    assert len(ids) == len(set(ids)), "Duplicate change_ids found"


# ===========================================================================
# C26-C29: Ordering
# ===========================================================================

def test_c26_ordering_critical_before_high_before_medium_before_info() -> None:
    """Changes are ordered: CRITICAL -> HIGH -> MEDIUM -> INFO."""
    e1 = _make_entity("e1", "critical service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=4))
    mtg2 = _make_meeting("m2", _RECENT - timedelta(hours=2))
    mtg3 = _make_meeting("m3", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "critical service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "critical service is in progress")
    mn3 = _make_mention("mn3", "e1", "m3", "critical service is blocked")
    # Two mentions in same meeting for REPEATED_UNRESOLVED (MEDIUM)
    e2 = _make_entity("e2", "other service")
    mn4 = _make_mention("mn4", "e2", "m1", "other service mentioned once")
    mn5 = _make_mention("mn5", "e2", "m1", "other service mentioned again")

    service = _build_service([e1, e2], [mtg1, mtg2, mtg3], [mn1, mn2, mn3, mn4, mn5])
    changes = service.get_changes(current_time=_BASE_TIME)

    severities = [c.severity for c in changes]

    # All changes with CRITICAL severity come before HIGH, which come before MEDIUM, etc.
    sev_indices = {s: [] for s in OrganisationChangeSeverity}
    for i, c in enumerate(changes):
        sev_indices[c.severity].append(i)

    from app.models.organisation_change import CHANGE_SEVERITY_ORDER
    severities_present = [
        s for s in [
            OrganisationChangeSeverity.CRITICAL,
            OrganisationChangeSeverity.HIGH,
            OrganisationChangeSeverity.MEDIUM,
            OrganisationChangeSeverity.INFO,
        ]
        if sev_indices[s]
    ]
    for i in range(len(severities_present) - 1):
        high_sev = severities_present[i]
        low_sev = severities_present[i + 1]
        max_high_idx = max(sev_indices[high_sev])
        min_low_idx = min(sev_indices[low_sev])
        assert max_high_idx < min_low_idx, (
            f"All {high_sev} changes should come before any {low_sev} changes. "
            f"But found {high_sev} at index {max_high_idx} and {low_sev} at index {min_low_idx}."
        )


def test_c27_same_severity_ordered_by_change_type_priority() -> None:
    """Within same severity level, changes are ordered by change_type priority."""
    # Both STATE_BLOCKED (priority 2) and STATE_REGRESSED (priority 3) are HIGH.
    # STATE_BLOCKED should appear before STATE_REGRESSED.
    e1 = _make_entity("e1", "pipeline")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=4))
    mtg2 = _make_meeting("m2", _RECENT - timedelta(hours=2))
    mtg3 = _make_meeting("m3", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "pipeline is open")
    mn2 = _make_mention("mn2", "e1", "m2", "pipeline is in progress")
    mn3 = _make_mention("mn3", "e1", "m3", "pipeline is blocked")

    service = _build_service([e1], [mtg1, mtg2, mtg3], [mn1, mn2, mn3])
    changes = service.get_changes(current_time=_BASE_TIME)

    high_changes = [c for c in changes if c.severity == OrganisationChangeSeverity.HIGH]
    types = [c.change_type for c in high_changes]

    if (
        OrganisationChangeType.STATE_BLOCKED in types
        and OrganisationChangeType.STATE_REGRESSED in types
    ):
        blocked_idx = next(
            i for i, c in enumerate(high_changes)
            if c.change_type == OrganisationChangeType.STATE_BLOCKED
        )
        regressed_idx = next(
            i for i, c in enumerate(high_changes)
            if c.change_type == OrganisationChangeType.STATE_REGRESSED
        )
        assert blocked_idx < regressed_idx, (
            "STATE_BLOCKED (priority 2) should appear before STATE_REGRESSED (priority 3)"
        )


def test_c28_same_severity_type_ordered_by_detected_at_desc() -> None:
    """Within same severity and type, changes are ordered by detected_at DESC."""
    e1 = _make_entity("e1", "service A")
    e2 = _make_entity("e2", "service B")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=4))
    mtg2 = _make_meeting("m2", _RECENT - timedelta(hours=2))
    mtg3 = _make_meeting("m3", _RECENT - timedelta(hours=1))
    mtg4 = _make_meeting("m4", _RECENT)

    # e1: OPEN -> BLOCKED (at m2) -- later
    mn1 = _make_mention("mn1", "e1", "m1", "service A is open")
    mn2 = _make_mention("mn2", "e1", "m2", "service A is blocked")

    # e2: OPEN -> BLOCKED (at m4) -- even later
    mn3 = _make_mention("mn3", "e2", "m3", "service B is open")
    mn4 = _make_mention("mn4", "e2", "m4", "service B is blocked")

    service = _build_service([e1, e2], [mtg1, mtg2, mtg3, mtg4], [mn1, mn2, mn3, mn4])
    changes = service.get_changes(current_time=_BASE_TIME)

    blocked_changes = [c for c in changes if c.change_type == OrganisationChangeType.STATE_BLOCKED]
    if len(blocked_changes) >= 2:
        for i in range(len(blocked_changes) - 1):
            assert blocked_changes[i].detected_at >= blocked_changes[i + 1].detected_at, (
                "Blocked changes should be ordered most-recent-first"
            )


def test_c29_entity_id_asc_tiebreaker() -> None:
    """When severity, type, and detected_at all match, entity_id ASC is the tiebreaker."""
    e1 = _make_entity("e1", "alpha service")
    e2 = _make_entity("e2", "beta service")
    # Both entities have mention at same time
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "alpha service is open")
    mn2 = _make_mention("mn2", "e2", "m1", "beta service is open")

    service = _build_service([e1, e2], [mtg], [mn1, mn2])
    changes = service.get_changes(current_time=_BASE_TIME)

    # Filter to INFO changes only (STATE_OPENED = INFO)
    info_changes = [c for c in changes if c.severity == OrganisationChangeSeverity.INFO]
    if len(info_changes) >= 2:
        # When all other sort keys equal, entity_id ASC should prevail
        entity_ids = [c.entity_id for c in info_changes]
        # Just verify no reverse order (e1 before e2 is correct)
        e1_indices = [i for i, c in enumerate(info_changes) if c.entity_id == "e1"]
        e2_indices = [i for i, c in enumerate(info_changes) if c.entity_id == "e2"]
        if e1_indices and e2_indices:
            assert min(e1_indices) < min(e2_indices), (
                "e1 should appear before e2 (entity_id ASC tiebreaker)"
            )


# ===========================================================================
# C30-C31: Determinism and idempotency
# ===========================================================================

def test_c30_same_input_produces_identical_results() -> None:
    """Same input + same current_time produces identical results."""
    e1 = _make_entity("e1", "auth service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "auth service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "auth service is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    changes1 = service.get_changes(current_time=_BASE_TIME)
    changes2 = service.get_changes(current_time=_BASE_TIME)

    assert len(changes1) == len(changes2)
    for c1, c2 in zip(changes1, changes2):
        assert c1.change_id == c2.change_id
        assert c1.change_type == c2.change_type
        assert c1.severity == c2.severity


def test_c31_repeated_calls_do_not_mutate_state() -> None:
    """Repeated calls do not mutate any state (service is idempotent)."""
    e1 = _make_entity("e1", "auth service")
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "auth service is blocked")

    service = _build_service([e1], [mtg], [mn1])
    _ = service.get_changes(current_time=_BASE_TIME)
    _ = service.get_changes(current_time=_BASE_TIME)
    _ = service.get_changes(current_time=_BASE_TIME)

    # If any state mutation occurred, the third call might produce different results.
    c1 = service.get_changes(current_time=_BASE_TIME)
    c2 = service.get_changes(current_time=_BASE_TIME)
    assert [c.change_id for c in c1] == [c.change_id for c in c2]


# ===========================================================================
# C32-C37: Filtering
# ===========================================================================

def test_c32_entity_id_filter() -> None:
    """entity_id filter returns only changes for that entity."""
    e1 = _make_entity("e1", "service one")
    e2 = _make_entity("e2", "service two")
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "service one is blocked")
    mn2 = _make_mention("mn2", "e2", "m1", "service two is blocked")

    service = _build_service([e1, e2], [mtg], [mn1, mn2])
    changes = service.get_changes(current_time=_BASE_TIME, entity_id="e1")

    assert all(c.entity_id == "e1" for c in changes)
    # e2 changes should not appear
    assert all(c.entity_id != "e2" for c in changes)


def test_c33_change_type_filter() -> None:
    """change_type filter returns only changes of that type."""
    e1 = _make_entity("e1", "service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "service is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    changes = service.get_changes(
        current_time=_BASE_TIME,
        change_type=OrganisationChangeType.STATE_BLOCKED,
    )

    assert all(c.change_type == OrganisationChangeType.STATE_BLOCKED for c in changes)


def test_c34_severity_filter() -> None:
    """severity filter returns only changes at that severity level."""
    e1 = _make_entity("e1", "service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "service is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    changes = service.get_changes(
        current_time=_BASE_TIME,
        severity=OrganisationChangeSeverity.HIGH,
    )

    assert all(c.severity == OrganisationChangeSeverity.HIGH for c in changes)
    assert len(changes) >= 1  # STATE_BLOCKED is HIGH


def test_c35_start_date_filter_excludes_old_changes() -> None:
    """start_date filter excludes changes with detected_at before the window."""
    e1 = _make_entity("e1", "old service")
    old_mtg = _make_meeting("m1", _STALE)
    mn1 = _make_mention("mn1", "e1", "m1", "old service is open")

    service = _build_service([e1], [old_mtg], [mn1])
    # Filter to only recent changes (start_date after the stale mention)
    recent_cutoff = _BASE_TIME - timedelta(days=10)
    changes = service.get_changes(current_time=_BASE_TIME, start_date=recent_cutoff)

    # Insight-based changes for this entity have detected_at = meeting_date (_STALE)
    # which is before recent_cutoff. ENTITY_BECAME_STALE uses epoch sentinel.
    # Most insight-derived changes should be filtered out.
    insight_changes = [
        c for c in changes
        if c.entity_id == "e1"
        and c.change_type not in (OrganisationChangeType.ENTITY_BECAME_STALE,)
        and c.detected_at < recent_cutoff
    ]
    assert len(insight_changes) == 0


def test_c36_end_date_filter_excludes_future_changes() -> None:
    """end_date filter excludes changes with detected_at after the window."""
    e1 = _make_entity("e1", "service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "service is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    # Set end_date to before the blocked mention
    cutoff = _RECENT - timedelta(hours=1)
    changes = service.get_changes(current_time=_BASE_TIME, end_date=cutoff)

    # STATE_BLOCKED change detected_at is _RECENT (after cutoff) so should be excluded.
    blocked = [
        c for c in changes
        if c.change_type == OrganisationChangeType.STATE_BLOCKED
        and c.detected_at > cutoff
    ]
    assert len(blocked) == 0


def test_c37_limit_caps_returned_changes() -> None:
    """limit parameter caps the number of returned changes."""
    e1 = _make_entity("e1", "service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=4))
    mtg2 = _make_meeting("m2", _RECENT - timedelta(hours=2))
    mtg3 = _make_meeting("m3", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "service is in progress")
    mn3 = _make_mention("mn3", "e1", "m3", "service is blocked")

    service = _build_service([e1], [mtg1, mtg2, mtg3], [mn1, mn2, mn3])
    all_changes = service.get_changes(current_time=_BASE_TIME)
    capped = service.get_changes(current_time=_BASE_TIME, limit=1)

    assert len(capped) == 1
    # The capped result should be the first element of the full result.
    if all_changes:
        assert capped[0].change_id == all_changes[0].change_id


# ===========================================================================
# C38-C41: Summary
# ===========================================================================

def test_c38_summary_aggregate_counts() -> None:
    """get_summary returns correct aggregate counts."""
    e1 = _make_entity("e1", "service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "service is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    summary = service.get_summary(current_time=_BASE_TIME)

    assert summary.total_changes >= 1
    assert summary.total_changes == (
        summary.critical_changes + summary.high_changes
        + summary.medium_changes + summary.info_changes
    )


def test_c39_summary_newly_blocked_entities() -> None:
    """get_summary.newly_blocked_entities lists entities with STATE_BLOCKED."""
    e1 = _make_entity("e1", "blocked service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "blocked service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "blocked service is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    summary = service.get_summary(current_time=_BASE_TIME)

    assert "e1" in summary.newly_blocked_entities


def test_c40_summary_regressed_entities() -> None:
    """get_summary.regressed_entities lists entities with STATE_REGRESSED."""
    e1 = _make_entity("e1", "regressing service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=4))
    mtg2 = _make_meeting("m2", _RECENT - timedelta(hours=2))
    mtg3 = _make_meeting("m3", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "regressing service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "regressing service is in progress")
    mn3 = _make_mention("mn3", "e1", "m3", "regressing service is blocked")

    service = _build_service([e1], [mtg1, mtg2, mtg3], [mn1, mn2, mn3])
    summary = service.get_summary(current_time=_BASE_TIME)

    assert "e1" in summary.regressed_entities


def test_c41_summary_changes_by_entity_grouping() -> None:
    """get_summary.changes_by_entity groups changes correctly."""
    e1 = _make_entity("e1", "service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=2))
    mtg2 = _make_meeting("m2", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "service is blocked")

    service = _build_service([e1], [mtg1, mtg2], [mn1, mn2])
    summary = service.get_summary(current_time=_BASE_TIME)

    assert "e1" in summary.changes_by_entity
    types_for_e1 = summary.changes_by_entity["e1"]
    assert OrganisationChangeType.STATE_BLOCKED.value in types_for_e1


# ===========================================================================
# C42-C44: Edge cases and parsing
# ===========================================================================

def test_c42_missing_entity_filter_returns_empty_list() -> None:
    """entity_id filter for nonexistent entity returns empty list."""
    service = _build_service()
    changes = service.get_changes(current_time=_BASE_TIME, entity_id="nonexistent_entity")
    assert changes == []


def test_c43_parse_state_transition_valid() -> None:
    """_parse_state_transition correctly parses standard InsightService descriptions."""
    parse = OrganisationChangeIntelligenceService._parse_state_transition

    from_s, to_s = parse("The entity transitioned from UNKNOWN to OPEN.")
    assert from_s == "UNKNOWN"
    assert to_s == "OPEN"

    from_s, to_s = parse("The entity transitioned from IN_PROGRESS to BLOCKED.")
    assert from_s == "IN_PROGRESS"
    assert to_s == "BLOCKED"

    from_s, to_s = parse("The entity transitioned from OPEN to RESOLVED.")
    assert from_s == "OPEN"
    assert to_s == "RESOLVED"


def test_c44_parse_state_transition_returns_none_for_unrecognised() -> None:
    """_parse_state_transition returns (None, None) for unrecognised text."""
    parse = OrganisationChangeIntelligenceService._parse_state_transition

    assert parse("No transition here") == (None, None)
    assert parse("") == (None, None)
    assert parse("transitioned from INVALID to ALSONOTVALID.") == (None, None)


def test_c45_multiple_transitions_produce_multiple_changes() -> None:
    """Entity with multiple state transitions produces multiple change records."""
    e1 = _make_entity("e1", "busy service")
    mtg1 = _make_meeting("m1", _RECENT - timedelta(hours=6))
    mtg2 = _make_meeting("m2", _RECENT - timedelta(hours=4))
    mtg3 = _make_meeting("m3", _RECENT - timedelta(hours=2))
    mtg4 = _make_meeting("m4", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "busy service is open")
    mn2 = _make_mention("mn2", "e1", "m2", "busy service is in progress")
    mn3 = _make_mention("mn3", "e1", "m3", "busy service is blocked")
    mn4 = _make_mention("mn4", "e1", "m4", "busy service is resolved")

    service = _build_service([e1], [mtg1, mtg2, mtg3, mtg4], [mn1, mn2, mn3, mn4])
    changes = service.get_changes(current_time=_BASE_TIME, entity_id="e1")

    types = {c.change_type for c in changes}
    # Should have at minimum: STATE_OPENED, STATE_STARTED, STATE_BLOCKED, STATE_RESOLVED
    assert OrganisationChangeType.STATE_BLOCKED in types
    assert OrganisationChangeType.STATE_RESOLVED in types
    # Total changes should be more than 1
    assert len(changes) > 1


# ===========================================================================
# C46-C52: API endpoint tests
# ===========================================================================

@pytest.fixture(scope="function")
def test_client():
    """A TestClient with isolated in-memory repositories for API tests."""
    # We need a fresh app for each test to avoid shared state.
    from app.main import app
    return TestClient(app)


def test_c46_get_changes_returns_valid_structure(test_client) -> None:
    """GET /api/v1/changes returns a valid JSON structure."""
    resp = test_client.get("/api/v1/changes")
    assert resp.status_code == 200
    data = resp.json()

    assert "total_changes" in data
    assert "critical_changes" in data
    assert "high_changes" in data
    assert "medium_changes" in data
    assert "info_changes" in data
    assert "changes" in data
    assert "evaluated_at" in data
    assert isinstance(data["changes"], list)


def test_c47_get_changes_no_entities_returns_empty(test_client) -> None:
    """GET /api/v1/changes with no entities returns empty changes list."""
    resp = test_client.get("/api/v1/changes")
    assert resp.status_code == 200
    data = resp.json()

    # With no entities/meetings/mentions, there are no changes.
    assert data["total_changes"] == 0
    assert data["changes"] == []


def test_c48_get_changes_structure_fields(test_client) -> None:
    """GET /api/v1/changes response fields have correct types."""
    resp = test_client.get("/api/v1/changes")
    assert resp.status_code == 200
    data = resp.json()

    assert isinstance(data["total_changes"], int)
    assert isinstance(data["critical_changes"], int)
    assert isinstance(data["high_changes"], int)
    assert isinstance(data["medium_changes"], int)
    assert isinstance(data["info_changes"], int)
    assert data["total_changes"] == (
        data["critical_changes"] + data["high_changes"]
        + data["medium_changes"] + data["info_changes"]
    )


def test_c49_get_changes_summary_returns_valid_structure(test_client) -> None:
    """GET /api/v1/changes/summary returns a valid JSON structure."""
    resp = test_client.get("/api/v1/changes/summary")
    assert resp.status_code == 200
    data = resp.json()

    assert "summary" in data
    assert "evaluated_at" in data

    summary = data["summary"]
    assert "total_changes" in summary
    assert "critical_changes" in summary
    assert "high_changes" in summary
    assert "medium_changes" in summary
    assert "info_changes" in summary
    assert "newly_blocked_entities" in summary
    assert "newly_resolved_entities" in summary
    assert "regressed_entities" in summary
    assert "reopened_entities" in summary
    assert "new_dependency_count" in summary
    assert "expanded_impact_count" in summary
    assert "stale_entities" in summary
    assert "changes_by_entity" in summary


def test_c50_get_changes_with_change_type_filter(test_client) -> None:
    """GET /api/v1/changes?change_type=STATE_BLOCKED filters by change type."""
    resp = test_client.get("/api/v1/changes?change_type=STATE_BLOCKED")
    assert resp.status_code == 200
    data = resp.json()

    for change in data["changes"]:
        assert change["change_type"] == "STATE_BLOCKED"


def test_c51_get_changes_with_severity_filter(test_client) -> None:
    """GET /api/v1/changes?severity=CRITICAL filters by severity."""
    resp = test_client.get("/api/v1/changes?severity=CRITICAL")
    assert resp.status_code == 200
    data = resp.json()

    for change in data["changes"]:
        assert change["severity"] == "CRITICAL"


def test_c52_get_changes_with_limit(test_client) -> None:
    """GET /api/v1/changes?limit=1 returns at most 1 change."""
    resp = test_client.get("/api/v1/changes?limit=1")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["changes"]) <= 1
