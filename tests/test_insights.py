"""Tests for the Insight & Change Detection Engine (Stage 8 of the pipeline).

All tests are fully deterministic -- no LLM calls, no network, no external database.

Coverage
--------

Service unit tests (no HTTP):
  I01. Entity with no observations returns empty insights list.
  I02. Entity with observations but UNKNOWN state generates UNKNOWN_STATE insight.
  I03. Valid state change generates STATE_CHANGED insight.
  I04. Transition to BLOCKED generates ISSUE_BLOCKED (in addition to STATE_CHANGED).
  I05. Transition to RESOLVED generates ISSUE_RESOLVED (in addition to STATE_CHANGED).
  I06. RESOLVED -> IN_PROGRESS creates REOPEN_ATTEMPT insight.
  I07. Invalid transition does not change temporal state (remains RESOLVED).
  I08. Repeated observations generate deterministic REPEATED_OBSERVATION insight.
  I09. No duplicate insights for the same underlying event.
  I10. Deterministic ordering: same repository state -> same insight order.
  I11. Stale unresolved entity generates STALE_ENTITY insight.
  I12. Resolved entity is NOT stale even if old.
  I13. Non-stale recent entity does not generate STALE_ENTITY.
  I14. Injected current_time is used (not real system clock).
  I18. Service does not mutate existing entity models.
  I19. Running service twice produces identical results.

Additional edge case tests:
  E01. Entity with no observations does not generate STALE_ENTITY.
  E02. Entity with no observations does not generate UNKNOWN_STATE.
  E03. STATE_CHANGED and ISSUE_BLOCKED are both produced for BLOCKED transition.
  E04. STATE_CHANGED and ISSUE_RESOLVED are both produced for RESOLVED transition.
  E05. REOPEN_ATTEMPT preserves evidence text from the triggering observation.
  E06. Severity mapping is correct for each insight type.
  E07. insight_id is a 16-character hex string.
  E08. deterministic_sort_key has correct format.
  E09. Full lifecycle (OPEN -> IN_PROGRESS -> BLOCKED -> RESOLVED) produces correct set.
  E10. PERSON entity with UNKNOWN state (no keywords) generates UNKNOWN_STATE.
  E11. Stale threshold of 0 days: entity with any past observation is stale.
  E12. Multiple entities never mix insights.
  E13. REOPEN_ATTEMPT evidence contains the attempted transition info.
  E14. REPEATED_OBSERVATION insight has correct meeting reference.

API endpoint tests (full stack via TestClient):
  I15. Unknown entity returns HTTP 404.
  I16. Existing entity with observations returns HTTP 200.
  I17. Empty insights response has correct structure (insight_count=0, insights=[]).
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
from app.models.insights import InsightType, InsightSeverity, INSIGHT_SEVERITY
from app.models.temporal import TemporalState
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.entity_service import EntityNotFoundError
from app.services.insight_service import InsightService, DEFAULT_STALE_THRESHOLD_DAYS
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


def _make_service(
    entities: list[CanonicalEntity],
    meetings: list[Meeting],
    mentions: list[EntityMention],
) -> InsightService:
    entity_repo = InMemoryEntityRepository()
    for e in entities:
        entity_repo.create(e)
    meeting_repo = InMemoryMeetingRepository()
    for m in meetings:
        meeting_repo.save(m)
    mention_repo = InMemoryMentionRepository()
    for m in mentions:
        mention_repo.create(m)
    return InsightService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


# ---------------------------------------------------------------------------
# Service Unit Tests
# ---------------------------------------------------------------------------

# I01 — Entity with no observations returns empty insights list

def test_i01_entity_with_no_observations_returns_empty():
    entity = _make_entity("e1", "Bug 1")
    service = _make_service([entity], [], [])
    insights = service.get_entity_insights("e1", current_time=_BASE_TIME)
    assert insights == []


# I02 — Entity with observations but UNKNOWN state generates UNKNOWN_STATE

def test_i02_entity_unknown_state_generates_unknown_state_insight():
    entity = _make_entity("e1", "Bug 1")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    # Source text with no state-bearing keywords → interpreted as UNKNOWN
    mention = _make_resolved_mention("mn1", "e1", "m1", "The bug was discussed in the meeting.")
    service = _make_service([entity], [meeting], [mention])

    current = _BASE_TIME + timedelta(days=1)
    insights = service.get_entity_insights("e1", current_time=current)

    types = [i.insight_type for i in insights]
    assert InsightType.UNKNOWN_STATE in types

    unknown = next(i for i in insights if i.insight_type == InsightType.UNKNOWN_STATE)
    assert unknown.entity_id == "e1"
    assert unknown.related_meeting_id is None
    assert unknown.severity == InsightSeverity.INFO


# I03 — Valid state change generates STATE_CHANGED insight

def test_i03_valid_state_change_generates_state_changed():
    entity = _make_entity("e1", "Bug 1")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    mention = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 has been reported.")
    service = _make_service([entity], [meeting], [mention])

    current = _BASE_TIME + timedelta(days=1)
    insights = service.get_entity_insights("e1", current_time=current)

    types = [i.insight_type for i in insights]
    assert InsightType.STATE_CHANGED in types

    sc = next(i for i in insights if i.insight_type == InsightType.STATE_CHANGED)
    assert sc.entity_id == "e1"
    assert sc.related_meeting_id == "m1"
    assert "UNKNOWN" in sc.description
    assert "OPEN" in sc.description
    assert sc.severity == InsightSeverity.INFO


# I04 — Transition to BLOCKED generates ISSUE_BLOCKED in addition to STATE_CHANGED

def test_i04_transition_to_blocked_generates_issue_blocked():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", "Sprint", offset_days=0)
    m2 = _make_meeting("m2", "Sync", offset_days=7)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 has started being worked on.")
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Bug 1 is blocked on infrastructure access.")
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    insights = service.get_entity_insights("e1", current_time=current)

    types = [i.insight_type for i in insights]
    assert InsightType.ISSUE_BLOCKED in types
    assert InsightType.STATE_CHANGED in types

    blocked = next(i for i in insights if i.insight_type == InsightType.ISSUE_BLOCKED)
    assert blocked.severity == InsightSeverity.WARNING
    assert blocked.related_meeting_id == "m2"
    assert "BLOCKED" in blocked.description


# I05 — Transition to RESOLVED generates ISSUE_RESOLVED in addition to STATE_CHANGED

def test_i05_transition_to_resolved_generates_issue_resolved():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", "Sprint", offset_days=0)
    m2 = _make_meeting("m2", "Retro", offset_days=14)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 has started being investigated.")
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Bug 1 has been resolved and closed.")
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=15)
    insights = service.get_entity_insights("e1", current_time=current)

    types = [i.insight_type for i in insights]
    assert InsightType.ISSUE_RESOLVED in types
    assert InsightType.STATE_CHANGED in types

    resolved = next(i for i in insights if i.insight_type == InsightType.ISSUE_RESOLVED)
    assert resolved.severity == InsightSeverity.INFO
    assert resolved.related_meeting_id == "m2"
    assert "RESOLVED" in resolved.description


# I06 — RESOLVED -> IN_PROGRESS creates REOPEN_ATTEMPT

def test_i06_resolved_to_in_progress_creates_reopen_attempt():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", "Sprint", offset_days=0)
    m2 = _make_meeting("m2", "Follow-up", offset_days=7)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 has been resolved and fixed.")
    # After RESOLVED, this observation tries to transition to IN_PROGRESS:
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Started working on bug 1 again.")
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    insights = service.get_entity_insights("e1", current_time=current)

    types = [i.insight_type for i in insights]
    assert InsightType.REOPEN_ATTEMPT in types

    reopen = next(i for i in insights if i.insight_type == InsightType.REOPEN_ATTEMPT)
    assert reopen.severity == InsightSeverity.WARNING
    assert reopen.related_meeting_id == "m2"
    # The entity temporal state must still be RESOLVED (handled by TemporalStateService)
    # — this is tested by the fact that REOPEN_ATTEMPT is generated, not STATE_CHANGED


# I07 — Invalid transition does not change temporal state (state remains RESOLVED)

def test_i07_invalid_transition_does_not_change_temporal_state():
    """After a reopen attempt, ISSUE_RESOLVED insight still holds (state=RESOLVED)."""
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", "Sprint", offset_days=0)
    m2 = _make_meeting("m2", "Follow-up", offset_days=7)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 resolved and done.")
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Started looking at this again.")
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    insights = service.get_entity_insights("e1", current_time=current)

    # REOPEN_ATTEMPT means the state did NOT change — no additional STATE_CHANGED
    # insight from the second observation.
    reopen_insights = [i for i in insights if i.insight_type == InsightType.REOPEN_ATTEMPT]
    assert len(reopen_insights) == 1

    # There should be no STATE_CHANGED for the attempted (invalid) transition.
    # (The first observation UNKNOWN->RESOLVED does generate STATE_CHANGED.)
    state_changed = [i for i in insights if i.insight_type == InsightType.STATE_CHANGED]
    # STATE_CHANGED only from the first (valid) transition — not the invalid one.
    for sc in state_changed:
        assert sc.related_meeting_id == "m1"


# I08 — Repeated observations generate deterministic REPEATED_OBSERVATION insight

def test_i08_repeated_observations_generate_deterministic_insight():
    entity = _make_entity("e1", "Bug 1")
    meeting = _make_meeting("m1", "Daily Standup", offset_days=0)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 mentioned again.")
    mn2 = _make_resolved_mention("mn2", "e1", "m1", "Bug 1 still under review.")
    mn3 = _make_resolved_mention("mn3", "e1", "m1", "Bug 1 is still pending.")
    service = _make_service([entity], [meeting], [mn1, mn2, mn3])

    current = _BASE_TIME + timedelta(days=1)
    insights1 = service.get_entity_insights("e1", current_time=current)
    insights2 = service.get_entity_insights("e1", current_time=current)

    rep1 = [i for i in insights1 if i.insight_type == InsightType.REPEATED_OBSERVATION]
    rep2 = [i for i in insights2 if i.insight_type == InsightType.REPEATED_OBSERVATION]
    assert len(rep1) == 1
    assert len(rep2) == 1
    assert rep1[0].insight_id == rep2[0].insight_id
    assert rep1[0].related_meeting_id == "m1"


# I09 — No duplicate insights for same event

def test_i09_no_duplicate_insights():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", "Sprint", offset_days=0)
    m2 = _make_meeting("m2", "Sync", offset_days=7)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 started.")
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Bug 1 resolved.")
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    insights = service.get_entity_insights("e1", current_time=current)

    # No duplicate insight_ids
    ids = [i.insight_id for i in insights]
    assert len(ids) == len(set(ids)), "Duplicate insight_ids detected"

    # No duplicate (entity_id, insight_type, related_meeting_id) for the same obs
    combos = [(i.entity_id, i.insight_type, i.related_meeting_id) for i in insights]
    assert len(combos) == len(set(combos)), "Duplicate insight combinations detected"


# I10 — Deterministic ordering: same input -> same order

def test_i10_deterministic_ordering():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", "Sprint 1", offset_days=0)
    m2 = _make_meeting("m2", "Sprint 2", offset_days=7)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 identified.")
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Bug 1 started being fixed.")
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    run1 = service.get_entity_insights("e1", current_time=current)
    run2 = service.get_entity_insights("e1", current_time=current)

    assert [i.insight_id for i in run1] == [i.insight_id for i in run2]
    assert [i.insight_type for i in run1] == [i.insight_type for i in run2]

    # Verify ordering: observed_at must be non-decreasing
    dates = [i.observed_at for i in run1]
    assert dates == sorted(dates)


# I11 — Stale unresolved entity generates STALE_ENTITY insight

def test_i11_stale_unresolved_entity_generates_stale_insight():
    entity = _make_entity("e1", "Old Bug")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    mention = _make_resolved_mention("mn1", "e1", "m1", "Old Bug was reported.")
    service = _make_service([entity], [meeting], [mention])

    # Simulate current time 35 days after the observation (> 30 day default)
    current = _BASE_TIME + timedelta(days=35)
    insights = service.get_entity_insights("e1", current_time=current)

    types = [i.insight_type for i in insights]
    assert InsightType.STALE_ENTITY in types

    stale = next(i for i in insights if i.insight_type == InsightType.STALE_ENTITY)
    assert stale.severity == InsightSeverity.WARNING
    assert stale.related_meeting_id is None
    assert "35" in stale.evidence or "34" in stale.evidence  # days stale


# I12 — Resolved entity is NOT stale even if old

def test_i12_resolved_entity_is_not_stale():
    entity = _make_entity("e1", "Old Bug")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    mention = _make_resolved_mention("mn1", "e1", "m1", "Old Bug has been resolved and fixed.")
    service = _make_service([entity], [meeting], [mention])

    # Simulate current time 100 days after the observation
    current = _BASE_TIME + timedelta(days=100)
    insights = service.get_entity_insights("e1", current_time=current)

    types = [i.insight_type for i in insights]
    assert InsightType.STALE_ENTITY not in types


# I13 — Non-stale recent entity does not generate STALE_ENTITY

def test_i13_non_stale_recent_entity_no_stale_insight():
    entity = _make_entity("e1", "Recent Bug")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    mention = _make_resolved_mention("mn1", "e1", "m1", "Recent Bug was reported.")
    service = _make_service([entity], [meeting], [mention])

    # 10 days after — well within 30-day threshold
    current = _BASE_TIME + timedelta(days=10)
    insights = service.get_entity_insights("e1", current_time=current)

    types = [i.insight_type for i in insights]
    assert InsightType.STALE_ENTITY not in types


# I14 — Injected current_time is used (not real system clock)

def test_i14_injected_current_time_is_used():
    entity = _make_entity("e1", "Bug")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    mention = _make_resolved_mention("mn1", "e1", "m1", "Bug was reported.")
    service = _make_service([entity], [meeting], [mention])

    # With time = 35 days after → stale
    current_stale = _BASE_TIME + timedelta(days=35)
    insights_stale = service.get_entity_insights("e1", current_time=current_stale)
    stale_types = [i.insight_type for i in insights_stale]
    assert InsightType.STALE_ENTITY in stale_types

    # With time = 5 days after → not stale
    current_fresh = _BASE_TIME + timedelta(days=5)
    insights_fresh = service.get_entity_insights("e1", current_time=current_fresh)
    fresh_types = [i.insight_type for i in insights_fresh]
    assert InsightType.STALE_ENTITY not in fresh_types


# I18 — Service does not mutate existing models

def test_i18_service_does_not_mutate_models():
    entity = _make_entity("e1", "Bug 1")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    mention = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 reported.")

    # Record original values
    original_entity_id = entity.entity_id
    original_entity_name = entity.canonical_name
    original_mention_id = mention.mention_id
    original_mention_status = mention.resolution_status
    original_mention_entity_id = mention.entity_id
    original_meeting_id = meeting.meeting_id

    service = _make_service([entity], [meeting], [mention])
    service.get_entity_insights("e1", current_time=_BASE_TIME + timedelta(days=1))

    # Nothing should have changed
    assert entity.entity_id == original_entity_id
    assert entity.canonical_name == original_entity_name
    assert mention.mention_id == original_mention_id
    assert mention.resolution_status == original_mention_status
    assert mention.entity_id == original_mention_entity_id
    assert meeting.meeting_id == original_meeting_id


# I19 — Running service twice produces identical results

def test_i19_running_service_twice_produces_identical_results():
    entity = _make_entity("e1", "Bug 1")
    m1 = _make_meeting("m1", "Sprint", offset_days=0)
    m2 = _make_meeting("m2", "Sync", offset_days=7)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 identified.")
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Bug 1 resolved.")
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    run1 = service.get_entity_insights("e1", current_time=current)
    run2 = service.get_entity_insights("e1", current_time=current)

    assert len(run1) == len(run2)
    for a, b in zip(run1, run2):
        assert a.insight_id == b.insight_id
        assert a.insight_type == b.insight_type
        assert a.title == b.title
        assert a.description == b.description
        assert a.severity == b.severity
        assert a.observed_at == b.observed_at
        assert a.related_meeting_id == b.related_meeting_id
        assert a.evidence == b.evidence
        assert a.deterministic_sort_key == b.deterministic_sort_key


# ---------------------------------------------------------------------------
# Additional Edge Case Tests
# ---------------------------------------------------------------------------

# E01 — Entity with no observations does not generate STALE_ENTITY

def test_e01_no_observations_no_stale():
    entity = _make_entity("e1", "Bug")
    service = _make_service([entity], [], [])
    current = _BASE_TIME + timedelta(days=365)  # Very old — but no observations
    insights = service.get_entity_insights("e1", current_time=current)
    types = [i.insight_type for i in insights]
    assert InsightType.STALE_ENTITY not in types


# E02 — Entity with no observations does not generate UNKNOWN_STATE

def test_e02_no_observations_no_unknown_state():
    entity = _make_entity("e1", "Bug")
    service = _make_service([entity], [], [])
    insights = service.get_entity_insights("e1", current_time=_BASE_TIME)
    types = [i.insight_type for i in insights]
    assert InsightType.UNKNOWN_STATE not in types


# E03 — STATE_CHANGED and ISSUE_BLOCKED both produced for BLOCKED transition

def test_e03_blocked_transition_produces_both_state_changed_and_issue_blocked():
    entity = _make_entity("e1", "Bug")
    m1 = _make_meeting("m1", "S1", offset_days=0)
    m2 = _make_meeting("m2", "S2", offset_days=7)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug started.")
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Bug is blocked on infra access.")
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    insights = service.get_entity_insights("e1", current_time=current)

    blocked_insights = [i for i in insights if i.insight_type == InsightType.ISSUE_BLOCKED]
    sc_insights = [i for i in insights if i.insight_type == InsightType.STATE_CHANGED]

    assert len(blocked_insights) >= 1
    assert len(sc_insights) >= 1
    # They must have DIFFERENT insight_ids
    blocked_ids = {i.insight_id for i in blocked_insights}
    sc_ids = {i.insight_id for i in sc_insights}
    assert blocked_ids.isdisjoint(sc_ids)


# E04 — STATE_CHANGED and ISSUE_RESOLVED both produced for RESOLVED transition

def test_e04_resolved_transition_produces_both_state_changed_and_issue_resolved():
    entity = _make_entity("e1", "Bug")
    m1 = _make_meeting("m1", "S1", offset_days=0)
    m2 = _make_meeting("m2", "S2", offset_days=7)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug started.")
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Bug resolved and done.")
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    insights = service.get_entity_insights("e1", current_time=current)

    resolved_insights = [i for i in insights if i.insight_type == InsightType.ISSUE_RESOLVED]
    sc_insights = [i for i in insights if i.insight_type == InsightType.STATE_CHANGED]

    assert len(resolved_insights) >= 1
    assert len(sc_insights) >= 1
    assert {i.insight_id for i in resolved_insights}.isdisjoint(
        {i.insight_id for i in sc_insights}
    )


# E05 — REOPEN_ATTEMPT preserves evidence text from triggering observation

def test_e05_reopen_attempt_preserves_evidence_text():
    entity = _make_entity("e1", "Bug")
    m1 = _make_meeting("m1", "S1", offset_days=0)
    m2 = _make_meeting("m2", "S2", offset_days=7)
    evidence_text = "We decided to start working on this bug again because it regressed."
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug resolved and fixed.")
    mn2 = _make_resolved_mention("mn2", "e1", "m2", evidence_text)
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    insights = service.get_entity_insights("e1", current_time=current)

    reopen = next(i for i in insights if i.insight_type == InsightType.REOPEN_ATTEMPT)
    assert reopen.evidence == evidence_text


# E06 — Severity mapping is correct for each insight type

def test_e06_severity_mapping_is_correct():
    expected = {
        InsightType.ISSUE_RESOLVED: InsightSeverity.INFO,
        InsightType.STATE_CHANGED: InsightSeverity.INFO,
        InsightType.REPEATED_OBSERVATION: InsightSeverity.INFO,
        InsightType.UNKNOWN_STATE: InsightSeverity.INFO,
        InsightType.STALE_ENTITY: InsightSeverity.WARNING,
        InsightType.ISSUE_BLOCKED: InsightSeverity.WARNING,
        InsightType.REOPEN_ATTEMPT: InsightSeverity.WARNING,
    }
    assert INSIGHT_SEVERITY == expected


# E07 — insight_id is a 16-character hex string

def test_e07_insight_id_is_16_char_hex():
    entity = _make_entity("e1", "Bug")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    mention = _make_resolved_mention("mn1", "e1", "m1", "Bug was reported.")
    service = _make_service([entity], [meeting], [mention])

    current = _BASE_TIME + timedelta(days=1)
    insights = service.get_entity_insights("e1", current_time=current)

    assert len(insights) > 0
    for insight in insights:
        assert len(insight.insight_id) == 16
        assert all(c in "0123456789abcdef" for c in insight.insight_id)


# E08 — deterministic_sort_key has correct format

def test_e08_sort_key_has_correct_format():
    entity = _make_entity("e1", "Bug")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    mention = _make_resolved_mention("mn1", "e1", "m1", "Bug was reported.")
    service = _make_service([entity], [meeting], [mention])

    current = _BASE_TIME + timedelta(days=1)
    insights = service.get_entity_insights("e1", current_time=current)

    for insight in insights:
        parts = insight.deterministic_sort_key.split("|")
        assert len(parts) == 4, f"Sort key must have 4 pipe-separated parts: {insight.deterministic_sort_key}"
        # Part 0: ISO datetime
        datetime.fromisoformat(parts[0])  # Should not raise
        # Part 1: entity_id
        assert parts[1] == "e1"
        # Part 2: insight_type value
        assert parts[2] in {t.value for t in InsightType}
        # Part 3: insight_id (16 hex chars)
        assert len(parts[3]) == 16


# E09 — Full lifecycle produces correct insight set

def test_e09_full_lifecycle_produces_correct_insights():
    """OPEN -> IN_PROGRESS -> BLOCKED -> RESOLVED should produce specific set."""
    entity = _make_entity("e1", "Full Lifecycle Bug")
    m1 = _make_meeting("m1", "W1", offset_days=0)
    m2 = _make_meeting("m2", "W2", offset_days=7)
    m3 = _make_meeting("m3", "W3", offset_days=14)
    m4 = _make_meeting("m4", "W4", offset_days=21)

    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug was reported and identified.")  # OPEN
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Started working on bug.")            # IN_PROGRESS
    mn3 = _make_resolved_mention("mn3", "e1", "m3", "Bug is blocked on infra access.")   # BLOCKED
    mn4 = _make_resolved_mention("mn4", "e1", "m4", "Bug resolved and closed.")           # RESOLVED

    service = _make_service([entity], [m1, m2, m3, m4], [mn1, mn2, mn3, mn4])

    current = _BASE_TIME + timedelta(days=22)
    insights = service.get_entity_insights("e1", current_time=current)

    types = [i.insight_type for i in insights]

    # Expected insight types:
    assert InsightType.STATE_CHANGED in types       # All 4 transitions
    assert InsightType.ISSUE_BLOCKED in types       # -> BLOCKED
    assert InsightType.ISSUE_RESOLVED in types      # -> RESOLVED
    assert InsightType.STALE_ENTITY not in types    # Only 22 days old and RESOLVED
    assert InsightType.UNKNOWN_STATE not in types   # Has clear state
    assert InsightType.REOPEN_ATTEMPT not in types  # No invalid transitions

    # There should be 4 STATE_CHANGED insights (UNKNOWN->OPEN, OPEN->IN_PROGRESS,
    # IN_PROGRESS->BLOCKED, BLOCKED->RESOLVED)
    sc_insights = [i for i in insights if i.insight_type == InsightType.STATE_CHANGED]
    assert len(sc_insights) == 4


# E10 — PERSON entity with UNKNOWN state generates UNKNOWN_STATE

def test_e10_person_entity_unknown_state_generates_unknown_state():
    entity = _make_entity("e1", "Rahul Kumar", EntityType.PERSON)
    meeting = _make_meeting("m1", "All Hands", offset_days=0)
    # No lifecycle keywords for a PERSON
    mention = _make_resolved_mention(
        "mn1", "e1", "m1", "Rahul Kumar presented the quarterly roadmap.",
        entity_type=EntityType.PERSON,
    )
    service = _make_service([entity], [meeting], [mention])

    current = _BASE_TIME + timedelta(days=1)
    insights = service.get_entity_insights("e1", current_time=current)

    types = [i.insight_type for i in insights]
    assert InsightType.UNKNOWN_STATE in types


# E11 — Stale threshold of 0 days: any past observation triggers STALE_ENTITY

def test_e11_stale_threshold_zero_any_past_observation_is_stale():
    entity = _make_entity("e1", "Bug")
    meeting = _make_meeting("m1", "Sprint", offset_days=0)
    mention = _make_resolved_mention("mn1", "e1", "m1", "Bug was reported.")
    service = _make_service([entity], [meeting], [mention])

    # current_time is 1 second after observation, threshold is 0 days
    # timedelta(days=0) means any age > 0 triggers staleness
    current = _BASE_TIME + timedelta(seconds=1)
    insights = service.get_entity_insights("e1", current_time=current, stale_threshold_days=0)

    types = [i.insight_type for i in insights]
    assert InsightType.STALE_ENTITY in types


# E12 — Multiple entities never mix insights

def test_e12_multiple_entities_do_not_mix_insights():
    e1 = _make_entity("e1", "Bug 1")
    e2 = _make_entity("e2", "Bug 2")
    m1 = _make_meeting("m1", "S1", offset_days=0)
    m2 = _make_meeting("m2", "S2", offset_days=7)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug 1 resolved.")
    mn2 = _make_resolved_mention("mn2", "e2", "m2", "Bug 2 blocked on access.")
    service = _make_service([e1, e2], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    insights_e1 = service.get_entity_insights("e1", current_time=current)
    insights_e2 = service.get_entity_insights("e2", current_time=current)

    for i in insights_e1:
        assert i.entity_id == "e1"
    for i in insights_e2:
        assert i.entity_id == "e2"

    ids_e1 = {i.insight_id for i in insights_e1}
    ids_e2 = {i.insight_id for i in insights_e2}
    assert ids_e1.isdisjoint(ids_e2)


# E13 — REOPEN_ATTEMPT description mentions the attempted transition

def test_e13_reopen_attempt_description_contains_attempted_transition():
    entity = _make_entity("e1", "Bug")
    m1 = _make_meeting("m1", "S1", offset_days=0)
    m2 = _make_meeting("m2", "S2", offset_days=7)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug resolved and done.")
    mn2 = _make_resolved_mention("mn2", "e1", "m2", "Started working on bug again.")
    service = _make_service([entity], [m1, m2], [mn1, mn2])

    current = _BASE_TIME + timedelta(days=8)
    insights = service.get_entity_insights("e1", current_time=current)

    reopen = next(i for i in insights if i.insight_type == InsightType.REOPEN_ATTEMPT)
    assert "RESOLVED" in reopen.description
    assert "IN_PROGRESS" in reopen.description or "remain" in reopen.description.lower()


# E14 — REPEATED_OBSERVATION insight has correct meeting reference

def test_e14_repeated_observation_has_correct_meeting_reference():
    entity = _make_entity("e1", "Bug")
    m1 = _make_meeting("m1", "Daily Standup", offset_days=0)
    m2 = _make_meeting("m2", "Sprint Review", offset_days=7)
    # m1: 2 mentions (repeated), m2: 1 mention (not repeated)
    mn1 = _make_resolved_mention("mn1", "e1", "m1", "Bug mentioned.")
    mn2 = _make_resolved_mention("mn2", "e1", "m1", "Bug still pending.")
    mn3 = _make_resolved_mention("mn3", "e1", "m2", "Bug resolved.")
    service = _make_service([entity], [m1, m2], [mn1, mn2, mn3])

    current = _BASE_TIME + timedelta(days=8)
    insights = service.get_entity_insights("e1", current_time=current)

    rep_insights = [i for i in insights if i.insight_type == InsightType.REPEATED_OBSERVATION]
    assert len(rep_insights) == 1
    assert rep_insights[0].related_meeting_id == "m1"


# ---------------------------------------------------------------------------
# API endpoint tests (full stack via TestClient)
# ---------------------------------------------------------------------------

@pytest.fixture
def insight_client():
    """Provide a fresh TestClient with isolated in-memory repositories injected
    via dependency_overrides (same pattern as test_temporal.py and test_memory.py).

    Returns (client, entity_repo, mention_repo, meeting_repo) so tests can
    populate data directly without going through the HTTP ingestion endpoints.
    """
    from app.main import app
    from app.api.entities import (
        get_insight_service,
        get_entity_service,
    )
    from app.services.entity_service import EntityService

    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    meeting_repo = InMemoryMeetingRepository()

    svc = InsightService(
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

    app.dependency_overrides[get_insight_service] = lambda: svc
    app.dependency_overrides[get_entity_service] = lambda: entity_service

    client = TestClient(app)
    yield client, entity_repo, mention_repo, meeting_repo

    app.dependency_overrides.pop(get_insight_service, None)
    app.dependency_overrides.pop(get_entity_service, None)


@pytest.fixture
def client():
    """Provide a simple TestClient for basic tests (e.g. 404 checks)."""
    from app.main import app
    from app.api import entities as entities_module
    from app.api import meetings as meetings_module

    entities_module._entity_repository = InMemoryEntityRepository()
    entities_module._mention_repository = InMemoryMentionRepository()
    meetings_module._meeting_repository = InMemoryMeetingRepository()

    return TestClient(app)


# I15 — Unknown entity API returns HTTP 404

def test_i15_unknown_entity_api_returns_404(client):
    resp = client.get("/api/v1/entities/nonexistent_entity_id/insights")
    assert resp.status_code == 404


# I16 — Existing entity with observations returns HTTP 200

def test_i16_existing_entity_with_observations_returns_200(insight_client):
    client, entity_repo, mention_repo, meeting_repo = insight_client
    entity_repo.create(_make_entity("e_api16", "Auth Bug"))
    meeting_repo.save(_make_meeting("m_api16", "Sprint", offset_days=0))
    mention_repo.create(_make_resolved_mention(
        "mn_api16", "e_api16", "m_api16",
        "Auth Bug has been resolved and closed."
    ))
    resp = client.get("/api/v1/entities/e_api16/insights")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_id"] == "e_api16"
    assert "insight_count" in data
    assert "insights" in data
    assert data["insight_count"] == len(data["insights"])


# I17 — Empty insights response has correct structure

def test_i17_empty_insights_response_structure(insight_client):
    client, entity_repo, *_ = insight_client
    entity_repo.create(_make_entity("e_api17", "Orphan Entity"))
    # No meetings or mentions — entity exists but has no observations
    resp = client.get("/api/v1/entities/e_api17/insights")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_id"] == "e_api17"
    assert data["insight_count"] == 0
    assert data["insights"] == []


# ---------------------------------------------------------------------------
# Model and schema structural tests
# ---------------------------------------------------------------------------

def test_insight_type_enum_values():
    """InsightType has the required set of values."""
    values = {t.value for t in InsightType}
    assert values == {
        "UNKNOWN_STATE",
        "STATE_CHANGED",
        "ISSUE_BLOCKED",
        "ISSUE_RESOLVED",
        "REOPEN_ATTEMPT",
        "REPEATED_OBSERVATION",
        "STALE_ENTITY",
    }


def test_insight_severity_enum_values():
    """InsightSeverity has INFO, WARNING, CRITICAL."""
    values = {s.value for s in InsightSeverity}
    assert values == {"INFO", "WARNING", "CRITICAL"}


def test_insight_type_is_str_enum():
    """InsightType is a str enum (serialises as plain string)."""
    assert isinstance(InsightType.STATE_CHANGED, str)
    assert InsightType.STATE_CHANGED == "STATE_CHANGED"


def test_insight_severity_is_str_enum():
    """InsightSeverity is a str enum."""
    assert isinstance(InsightSeverity.WARNING, str)
    assert InsightSeverity.WARNING == "WARNING"


def test_entity_insight_is_pydantic():
    """EntityInsight is a Pydantic BaseModel."""
    from pydantic import BaseModel
    from app.models.insights import EntityInsight
    assert issubclass(EntityInsight, BaseModel)


def test_insight_service_raises_for_unknown_entity():
    """InsightService raises EntityNotFoundError for unknown entity_id."""
    service = _make_service([], [], [])
    with pytest.raises(EntityNotFoundError):
        service.get_entity_insights("nonexistent_entity_id")


def test_default_stale_threshold_is_30_days():
    """DEFAULT_STALE_THRESHOLD_DAYS is 30."""
    assert DEFAULT_STALE_THRESHOLD_DAYS == 30
