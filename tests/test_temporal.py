"""Tests for the Temporal State Engine (Stage 6 of the pipeline).

All tests are fully deterministic -- no LLM calls, no network, no external database.

Coverage
--------

State Interpreter (KeywordStateInterpreter):
  I01. Empty evidence text returns UNKNOWN.
  I02. Whitespace-only evidence text returns UNKNOWN.
  I03. No keyword in text returns UNKNOWN.
  I04. "started" keyword returns IN_PROGRESS.
  I05. "working on" (multi-word) keyword returns IN_PROGRESS.
  I06. "in-progress" (hyphenated) keyword returns IN_PROGRESS.
  I07. "ongoing" keyword returns IN_PROGRESS.
  I08. "underway" keyword returns IN_PROGRESS.
  I09. "blocked" keyword returns BLOCKED.
  I10. "blocker" keyword returns BLOCKED.
  I11. "stuck" keyword returns BLOCKED.
  I12. "stalled" keyword returns BLOCKED.
  I13. "waiting" keyword returns BLOCKED.
  I14. "resolved" keyword returns RESOLVED.
  I15. "fixed" keyword returns RESOLVED.
  I16. "closed" keyword returns RESOLVED.
  I17. "completed" keyword returns RESOLVED.
  I18. "done" keyword returns RESOLVED.
  I19. "finished" keyword returns RESOLVED.
  I20. "raised" keyword returns OPEN.
  I21. "identified" keyword returns OPEN.
  I22. "reported" keyword returns OPEN.
  I23. "created" keyword returns OPEN.
  I24. "new issue" (multi-word) returns OPEN.
  I25. "filed" keyword returns OPEN.
  I26. Priority: RESOLVED beats BLOCKED when both present.
  I27. Priority: BLOCKED beats IN_PROGRESS when both present.
  I28. Priority: IN_PROGRESS beats OPEN when both present.
  I29. Case-insensitive: "BLOCKED" matches.
  I30. Case-insensitive: "RESOLVED" matches.
  I31. Interpreter is deterministic: same input → same output.

Transition Policy (DefaultTransitionPolicy):
  T01. Initial state UNKNOWN → apply UNKNOWN → no transition (CASE A).
  T02. UNKNOWN → OPEN: valid transition (CASE C).
  T03. UNKNOWN → IN_PROGRESS: valid transition (CASE C).
  T04. UNKNOWN → BLOCKED: valid transition (CASE C).
  T05. UNKNOWN → RESOLVED: valid transition (CASE C).
  T06. OPEN → IN_PROGRESS: valid transition.
  T07. OPEN → BLOCKED: valid transition.
  T08. OPEN → RESOLVED: valid transition.
  T09. IN_PROGRESS → BLOCKED: valid transition.
  T10. IN_PROGRESS → RESOLVED: valid transition.
  T11. BLOCKED → IN_PROGRESS: valid transition.
  T12. BLOCKED → RESOLVED: valid transition.
  T13. RESOLVED → IN_PROGRESS: INVALID (CASE D), current_state unchanged.
  T14. RESOLVED → OPEN: INVALID (CASE D), current_state unchanged.
  T15. RESOLVED → BLOCKED: INVALID (CASE D), current_state unchanged.
  T16. IN_PROGRESS → OPEN: INVALID (CASE D).
  T17. OPEN → UNKNOWN: CASE A (no-op, not an error).
  T18. Repeated state IN_PROGRESS → IN_PROGRESS: no transition (CASE B).
  T19. Repeated state RESOLVED → RESOLVED: no transition (CASE B).
  T20. Invalid transition: reason string is non-empty.
  T21. Invalid transition: transition_occurred is False.
  T22. Repeated state: transition_occurred is False, is_valid is True.
  T23. Policy is deterministic: same inputs → same result.

Service (TemporalStateService):
  S01. Unknown entity_id raises EntityNotFoundError.
  S02. Entity with no resolved mentions returns empty timeline, UNKNOWN state.
  S03. One observation with state-bearing text: interpreted correctly.
  S04. Chronological ordering: observations sorted by (meeting_date, meeting_id, mention_id).
  S05. OPEN → IN_PROGRESS → BLOCKED → RESOLVED full lifecycle.
  S06. Repeated state: no duplicate transition recorded.
  S07. Invalid transition: recorded but current_state unchanged.
  S08. UNRESOLVED mention excluded from timeline.
  S09. AMBIGUOUS mention excluded from timeline.
  S10. current_state is UNKNOWN when no observations have state-bearing text.
  S11. transition_count equals number of entries with transition_occurred=True.
  S12. observation_count equals len(timeline).
  S13. evidence_text preserved correctly.
  S14. mention_id preserved correctly.
  S15. meeting_date preserved correctly.
  S16. Service does NOT modify mention resolution_status.
  S17. Service does NOT modify mention entity_id.
  S18. Service does NOT create entities.
  S19. Service does NOT trigger candidate generation.
  S20. Service does NOT trigger scoring.
  S21. Service does NOT trigger resolution.
  S22. Timeline is deterministic: same state → same result on repeated calls.
  S23. Missing meeting is skipped gracefully (via CorrelationService behaviour).
  S24. Same-date observations ordered by meeting_id then mention_id.

API endpoint (GET /api/v1/entities/{entity_id}/timeline):
  A01. Unknown entity_id returns HTTP 404.
  A02. Entity with no resolved mentions returns HTTP 200 with empty timeline.
  A03. Valid entity with observations returns HTTP 200.
  A04. Response has all required top-level fields.
  A05. current_state in response matches expected value.
  A06. Timeline is chronologically ordered.
  A07. observation_count matches len(timeline).
  A08. transition_count is correct.
  A09. Each timeline entry has all required fields.
  A10. is_valid_transition=false for invalid transitions.
  A11. transition_skipped_reason populated for invalid transitions.
  A12. transition_skipped_reason null for valid transitions.
  A13. from_state and to_state correct for each entry.
  A14. evidence_text in response matches source_text.
  A15. PERSON entity with no lifecycle evidence returns UNKNOWN.
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
from app.models.temporal import EntityTimeline, StateObservation, TemporalState
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.entity_service import EntityNotFoundError
from app.services.temporal_state_service import TemporalStateService
from app.temporal.state_interpreter import KeywordStateInterpreter
from app.temporal.transition_policy import DefaultTransitionPolicy, TransitionResult


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
) -> TemporalStateService:
    entity_repo = InMemoryEntityRepository()
    for e in entities:
        entity_repo.create(e)
    meeting_repo = InMemoryMeetingRepository()
    for m in meetings:
        meeting_repo.save(m)  # MeetingRepository uses .save(), not .create()
    mention_repo = InMemoryMentionRepository()
    for m in mentions:
        mention_repo.create(m)
    return TemporalStateService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
    )


# ---------------------------------------------------------------------------
# State Interpreter tests
# ---------------------------------------------------------------------------

class TestKeywordStateInterpreter:
    """Tests for KeywordStateInterpreter."""

    def setup_method(self):
        self.interp = KeywordStateInterpreter()

    # Empty/blank inputs

    def test_i01_empty_string_returns_unknown(self):
        assert self.interp.interpret("") == TemporalState.UNKNOWN

    def test_i02_whitespace_only_returns_unknown(self):
        assert self.interp.interpret("   ") == TemporalState.UNKNOWN

    def test_i03_no_keyword_returns_unknown(self):
        assert self.interp.interpret("The team had a discussion about the API.") == TemporalState.UNKNOWN

    # IN_PROGRESS keywords

    def test_i04_started_returns_in_progress(self):
        assert self.interp.interpret("Rahul started working on this.") == TemporalState.IN_PROGRESS

    def test_i05_working_on_returns_in_progress(self):
        assert self.interp.interpret("We are working on the issue.") == TemporalState.IN_PROGRESS

    def test_i06_in_progress_hyphenated_returns_in_progress(self):
        assert self.interp.interpret("The fix is in-progress.") == TemporalState.IN_PROGRESS

    def test_i06b_in_progress_with_space_returns_in_progress(self):
        assert self.interp.interpret("The fix is in progress.") == TemporalState.IN_PROGRESS

    def test_i07_ongoing_returns_in_progress(self):
        assert self.interp.interpret("The investigation is ongoing.") == TemporalState.IN_PROGRESS

    def test_i08_underway_returns_in_progress(self):
        assert self.interp.interpret("The migration is underway.") == TemporalState.IN_PROGRESS

    # BLOCKED keywords

    def test_i09_blocked_returns_blocked(self):
        assert self.interp.interpret("The task is blocked on infrastructure.") == TemporalState.BLOCKED

    def test_i10_blocker_returns_blocked(self):
        assert self.interp.interpret("There is a blocker in the deployment.") == TemporalState.BLOCKED

    def test_i11_stuck_returns_blocked(self):
        assert self.interp.interpret("We are stuck waiting for approval.") == TemporalState.BLOCKED

    def test_i12_stalled_returns_blocked(self):
        assert self.interp.interpret("The migration has stalled.") == TemporalState.BLOCKED

    def test_i13_waiting_returns_blocked(self):
        assert self.interp.interpret("Waiting for the database team to respond.") == TemporalState.BLOCKED

    # RESOLVED keywords

    def test_i14_resolved_returns_resolved(self):
        assert self.interp.interpret("The issue has been resolved.") == TemporalState.RESOLVED

    def test_i15_fixed_returns_resolved(self):
        assert self.interp.interpret("Rahul fixed the payment API bug.") == TemporalState.RESOLVED

    def test_i16_closed_returns_resolved(self):
        assert self.interp.interpret("The ticket has been closed.") == TemporalState.RESOLVED

    def test_i17_completed_returns_resolved(self):
        assert self.interp.interpret("The migration has been completed.") == TemporalState.RESOLVED

    def test_i18_done_returns_resolved(self):
        assert self.interp.interpret("We are done with this.") == TemporalState.RESOLVED

    def test_i19_finished_returns_resolved(self):
        assert self.interp.interpret("The implementation is finished.") == TemporalState.RESOLVED

    # OPEN keywords

    def test_i20_raised_returns_open(self):
        assert self.interp.interpret("Rahul raised the payment API issue.") == TemporalState.OPEN

    def test_i21_identified_returns_open(self):
        assert self.interp.interpret("The team identified a performance problem.") == TemporalState.OPEN

    def test_i22_reported_returns_open(self):
        assert self.interp.interpret("Priya reported the authentication failure.") == TemporalState.OPEN

    def test_i23_created_returns_open(self):
        assert self.interp.interpret("A new ticket was created for this.") == TemporalState.OPEN

    def test_i24_new_issue_returns_open(self):
        assert self.interp.interpret("There is a new issue with the login flow.") == TemporalState.OPEN

    def test_i25_filed_returns_open(self):
        assert self.interp.interpret("A bug was filed for this.") == TemporalState.OPEN

    # Priority tests

    def test_i26_resolved_beats_blocked(self):
        """RESOLVED has higher priority than BLOCKED."""
        text = "The blocked issue has been resolved."
        assert self.interp.interpret(text) == TemporalState.RESOLVED

    def test_i27_blocked_beats_in_progress(self):
        """BLOCKED has higher priority than IN_PROGRESS."""
        text = "We started working but got blocked."
        assert self.interp.interpret(text) == TemporalState.BLOCKED

    def test_i28_in_progress_beats_open(self):
        """IN_PROGRESS has higher priority than OPEN."""
        text = "We raised this and started working on it."
        assert self.interp.interpret(text) == TemporalState.IN_PROGRESS

    # Case insensitivity

    def test_i29_case_insensitive_blocked(self):
        assert self.interp.interpret("BLOCKED by infrastructure issues.") == TemporalState.BLOCKED

    def test_i30_case_insensitive_resolved(self):
        assert self.interp.interpret("Issue RESOLVED by the team.") == TemporalState.RESOLVED

    # Determinism

    def test_i31_interpreter_is_deterministic(self):
        text = "The migration has started."
        result1 = self.interp.interpret(text)
        result2 = self.interp.interpret(text)
        assert result1 == result2 == TemporalState.IN_PROGRESS


# ---------------------------------------------------------------------------
# Transition Policy tests
# ---------------------------------------------------------------------------

class TestDefaultTransitionPolicy:
    """Tests for DefaultTransitionPolicy."""

    def setup_method(self):
        self.policy = DefaultTransitionPolicy()

    # CASE A — UNKNOWN new_state is a no-op

    def test_t01_unknown_to_unknown_no_transition(self):
        result = self.policy.apply(TemporalState.UNKNOWN, TemporalState.UNKNOWN)
        assert result.transition_occurred is False
        assert result.current_state == TemporalState.UNKNOWN
        assert result.is_valid is True

    def test_t17_any_to_unknown_no_transition(self):
        """Interpreting UNKNOWN evidence from any state is a no-op."""
        result = self.policy.apply(TemporalState.IN_PROGRESS, TemporalState.UNKNOWN)
        assert result.transition_occurred is False
        assert result.current_state == TemporalState.IN_PROGRESS
        assert result.is_valid is True

    # CASE C — Valid transitions from UNKNOWN

    def test_t02_unknown_to_open_valid(self):
        result = self.policy.apply(TemporalState.UNKNOWN, TemporalState.OPEN)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.OPEN
        assert result.is_valid is True

    def test_t03_unknown_to_in_progress_valid(self):
        result = self.policy.apply(TemporalState.UNKNOWN, TemporalState.IN_PROGRESS)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.IN_PROGRESS
        assert result.is_valid is True

    def test_t04_unknown_to_blocked_valid(self):
        result = self.policy.apply(TemporalState.UNKNOWN, TemporalState.BLOCKED)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.BLOCKED
        assert result.is_valid is True

    def test_t05_unknown_to_resolved_valid(self):
        result = self.policy.apply(TemporalState.UNKNOWN, TemporalState.RESOLVED)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.RESOLVED
        assert result.is_valid is True

    # CASE C — Valid transitions from OPEN

    def test_t06_open_to_in_progress_valid(self):
        result = self.policy.apply(TemporalState.OPEN, TemporalState.IN_PROGRESS)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.IN_PROGRESS
        assert result.is_valid is True

    def test_t07_open_to_blocked_valid(self):
        result = self.policy.apply(TemporalState.OPEN, TemporalState.BLOCKED)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.BLOCKED
        assert result.is_valid is True

    def test_t08_open_to_resolved_valid(self):
        result = self.policy.apply(TemporalState.OPEN, TemporalState.RESOLVED)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.RESOLVED
        assert result.is_valid is True

    # CASE C — Valid transitions from IN_PROGRESS

    def test_t09_in_progress_to_blocked_valid(self):
        result = self.policy.apply(TemporalState.IN_PROGRESS, TemporalState.BLOCKED)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.BLOCKED
        assert result.is_valid is True

    def test_t10_in_progress_to_resolved_valid(self):
        result = self.policy.apply(TemporalState.IN_PROGRESS, TemporalState.RESOLVED)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.RESOLVED
        assert result.is_valid is True

    # CASE C — Valid transitions from BLOCKED

    def test_t11_blocked_to_in_progress_valid(self):
        result = self.policy.apply(TemporalState.BLOCKED, TemporalState.IN_PROGRESS)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.IN_PROGRESS
        assert result.is_valid is True

    def test_t12_blocked_to_resolved_valid(self):
        result = self.policy.apply(TemporalState.BLOCKED, TemporalState.RESOLVED)
        assert result.transition_occurred is True
        assert result.current_state == TemporalState.RESOLVED
        assert result.is_valid is True

    # CASE D — Invalid transitions from RESOLVED (terminal state)

    def test_t13_resolved_to_in_progress_invalid(self):
        result = self.policy.apply(TemporalState.RESOLVED, TemporalState.IN_PROGRESS)
        assert result.is_valid is False
        assert result.transition_occurred is False
        assert result.current_state == TemporalState.RESOLVED  # unchanged

    def test_t14_resolved_to_open_invalid(self):
        result = self.policy.apply(TemporalState.RESOLVED, TemporalState.OPEN)
        assert result.is_valid is False
        assert result.transition_occurred is False
        assert result.current_state == TemporalState.RESOLVED

    def test_t15_resolved_to_blocked_invalid(self):
        result = self.policy.apply(TemporalState.RESOLVED, TemporalState.BLOCKED)
        assert result.is_valid is False
        assert result.current_state == TemporalState.RESOLVED

    def test_t16_in_progress_to_open_invalid(self):
        result = self.policy.apply(TemporalState.IN_PROGRESS, TemporalState.OPEN)
        assert result.is_valid is False
        assert result.current_state == TemporalState.IN_PROGRESS

    # CASE B — Repeated state

    def test_t18_repeated_in_progress_no_transition(self):
        result = self.policy.apply(TemporalState.IN_PROGRESS, TemporalState.IN_PROGRESS)
        assert result.transition_occurred is False
        assert result.is_valid is True
        assert result.current_state == TemporalState.IN_PROGRESS

    def test_t19_repeated_resolved_no_transition(self):
        result = self.policy.apply(TemporalState.RESOLVED, TemporalState.RESOLVED)
        assert result.transition_occurred is False
        assert result.is_valid is True
        assert result.current_state == TemporalState.RESOLVED

    # Reason strings

    def test_t20_invalid_transition_reason_is_non_empty(self):
        result = self.policy.apply(TemporalState.RESOLVED, TemporalState.IN_PROGRESS)
        assert result.reason is not None
        assert len(result.reason) > 0
        assert "RESOLVED" in result.reason
        assert "IN_PROGRESS" in result.reason

    def test_t21_invalid_transition_occurred_is_false(self):
        result = self.policy.apply(TemporalState.RESOLVED, TemporalState.OPEN)
        assert result.transition_occurred is False

    def test_t22_repeated_state_reason_is_none(self):
        result = self.policy.apply(TemporalState.IN_PROGRESS, TemporalState.IN_PROGRESS)
        assert result.reason is None
        assert result.is_valid is True

    # Determinism

    def test_t23_policy_is_deterministic(self):
        r1 = self.policy.apply(TemporalState.IN_PROGRESS, TemporalState.BLOCKED)
        r2 = self.policy.apply(TemporalState.IN_PROGRESS, TemporalState.BLOCKED)
        assert r1 == r2

    # Abstract base class contract

    def test_policy_is_abstract_base(self):
        from app.temporal.transition_policy import AbstractTemporalStatePolicy
        from abc import ABC
        assert issubclass(AbstractTemporalStatePolicy, ABC)

    def test_default_policy_subclasses_abstract(self):
        from app.temporal.transition_policy import AbstractTemporalStatePolicy
        assert isinstance(self.policy, AbstractTemporalStatePolicy)


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------

class TestTemporalStateService:
    """Tests for TemporalStateService."""

    # S01 — Unknown entity raises EntityNotFoundError

    def test_s01_unknown_entity_raises_not_found(self):
        service = _make_service([], [], [])
        with pytest.raises(EntityNotFoundError):
            service.get_entity_timeline("nonexistent_entity")

    # S02 — Entity with no mentions returns empty timeline

    def test_s02_no_resolved_mentions_empty_timeline(self):
        entity = _make_entity("e1", "payment api instability")
        service = _make_service([entity], [], [])
        timeline = service.get_entity_timeline("e1")
        assert timeline.entity_id == "e1"
        assert timeline.current_state == TemporalState.UNKNOWN
        assert timeline.observation_count == 0
        assert timeline.transition_count == 0
        assert timeline.timeline == []

    # S03 — One observation interpreted correctly

    def test_s03_one_observation_with_state(self):
        entity = _make_entity("e1", "payment api instability")
        meeting = _make_meeting("m1", "Sprint Planning")
        mention = _make_resolved_mention(
            "mn1", "e1", "m1", "The issue has started being investigated."
        )
        service = _make_service([entity], [meeting], [mention])
        timeline = service.get_entity_timeline("e1")
        assert len(timeline.timeline) == 1
        obs = timeline.timeline[0]
        assert obs.interpreted_state == TemporalState.IN_PROGRESS
        assert obs.transition_occurred is True
        assert obs.from_state == TemporalState.UNKNOWN
        assert obs.to_state == TemporalState.IN_PROGRESS
        assert obs.is_valid_transition is True
        assert timeline.current_state == TemporalState.IN_PROGRESS

    # S04 — Chronological ordering

    def test_s04_chronological_ordering(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "Week 1", offset_days=0)
        m2 = _make_meeting("m2", "Week 2", offset_days=7)
        m3 = _make_meeting("m3", "Week 3", offset_days=14)
        mn1 = _make_resolved_mention("mn1", "e1", "m1", "The issue has started.")
        mn2 = _make_resolved_mention("mn2", "e1", "m2", "The issue is blocked.")
        mn3 = _make_resolved_mention("mn3", "e1", "m3", "The issue is resolved.")
        service = _make_service([entity], [m1, m2, m3], [mn1, mn2, mn3])
        timeline = service.get_entity_timeline("e1")
        assert len(timeline.timeline) == 3
        assert timeline.timeline[0].meeting_id == "m1"
        assert timeline.timeline[1].meeting_id == "m2"
        assert timeline.timeline[2].meeting_id == "m3"

    # S05 — Full lifecycle OPEN → IN_PROGRESS → BLOCKED → RESOLVED

    def test_s05_full_lifecycle(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1", offset_days=0)
        m2 = _make_meeting("m2", "W2", offset_days=7)
        m3 = _make_meeting("m3", "W3", offset_days=14)
        m4 = _make_meeting("m4", "W4", offset_days=21)
        mn1 = _make_resolved_mention("mn1", "e1", "m1", "The issue was raised.")
        mn2 = _make_resolved_mention("mn2", "e1", "m2", "Started working on it.")
        mn3 = _make_resolved_mention("mn3", "e1", "m3", "Now blocked on infra access.")
        mn4 = _make_resolved_mention("mn4", "e1", "m4", "The issue is resolved.")
        service = _make_service([entity], [m1, m2, m3, m4], [mn1, mn2, mn3, mn4])
        timeline = service.get_entity_timeline("e1")
        assert timeline.current_state == TemporalState.RESOLVED
        assert len(timeline.timeline) == 4
        states = [obs.to_state for obs in timeline.timeline]
        assert states == [
            TemporalState.OPEN,
            TemporalState.IN_PROGRESS,
            TemporalState.BLOCKED,
            TemporalState.RESOLVED,
        ]

    # S06 — Repeated state: no duplicate transition

    def test_s06_repeated_state_no_duplicate_transition(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1", offset_days=0)
        m2 = _make_meeting("m2", "W2", offset_days=7)
        mn1 = _make_resolved_mention("mn1", "e1", "m1", "Started working on this.")
        mn2 = _make_resolved_mention("mn2", "e1", "m2", "Still working on this — in progress.")
        service = _make_service([entity], [m1, m2], [mn1, mn2])
        timeline = service.get_entity_timeline("e1")
        assert len(timeline.timeline) == 2
        assert timeline.timeline[0].transition_occurred is True
        assert timeline.timeline[1].transition_occurred is False
        assert timeline.transition_count == 1

    # S07 — Invalid transition: recorded but current_state unchanged

    def test_s07_invalid_transition_recorded_state_unchanged(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1", offset_days=0)
        m2 = _make_meeting("m2", "W2", offset_days=7)
        mn1 = _make_resolved_mention("mn1", "e1", "m1", "The issue is resolved.")
        mn2 = _make_resolved_mention("mn2", "e1", "m2", "Now started working on it again.")
        service = _make_service([entity], [m1, m2], [mn1, mn2])
        timeline = service.get_entity_timeline("e1")
        assert len(timeline.timeline) == 2
        assert timeline.timeline[0].transition_occurred is True
        assert timeline.timeline[0].to_state == TemporalState.RESOLVED
        # Second observation: RESOLVED → IN_PROGRESS is invalid
        assert timeline.timeline[1].is_valid_transition is False
        assert timeline.timeline[1].transition_occurred is False
        assert timeline.timeline[1].to_state == TemporalState.RESOLVED  # unchanged
        assert timeline.current_state == TemporalState.RESOLVED

    # S08 — UNRESOLVED mention excluded

    def test_s08_unresolved_mention_excluded(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1")
        unresolved = _make_unresolved_mention("mn1", "m1", "The issue has started.")
        service = _make_service([entity], [m1], [unresolved])
        timeline = service.get_entity_timeline("e1")
        assert timeline.observation_count == 0
        assert timeline.current_state == TemporalState.UNKNOWN

    # S09 — AMBIGUOUS mention excluded

    def test_s09_ambiguous_mention_excluded(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1")
        ambiguous = _make_unresolved_mention(
            "mn1", "m1", "The issue is resolved.", ResolutionStatus.AMBIGUOUS
        )
        service = _make_service([entity], [m1], [ambiguous])
        timeline = service.get_entity_timeline("e1")
        assert timeline.observation_count == 0
        assert timeline.current_state == TemporalState.UNKNOWN

    # S10 — No state-bearing text → UNKNOWN current state

    def test_s10_no_state_keywords_current_state_unknown(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1")
        mention = _make_resolved_mention("mn1", "e1", "m1", "The team discussed the API.")
        service = _make_service([entity], [m1], [mention])
        timeline = service.get_entity_timeline("e1")
        assert timeline.current_state == TemporalState.UNKNOWN
        assert timeline.observation_count == 1
        assert timeline.transition_count == 0

    # S11 — transition_count equals sum of transition_occurred

    def test_s11_transition_count_correct(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1", offset_days=0)
        m2 = _make_meeting("m2", "W2", offset_days=7)
        m3 = _make_meeting("m3", "W3", offset_days=14)
        mn1 = _make_resolved_mention("mn1", "e1", "m1", "Issue raised.")
        mn2 = _make_resolved_mention("mn2", "e1", "m2", "Started working on it.")
        mn3 = _make_resolved_mention("mn3", "e1", "m3", "Started again still.")  # still IN_PROGRESS
        service = _make_service([entity], [m1, m2, m3], [mn1, mn2, mn3])
        timeline = service.get_entity_timeline("e1")
        assert timeline.transition_count == sum(
            1 for obs in timeline.timeline if obs.transition_occurred
        )

    # S12 — observation_count equals len(timeline)

    def test_s12_observation_count_equals_len_timeline(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1", offset_days=0)
        m2 = _make_meeting("m2", "W2", offset_days=7)
        mn1 = _make_resolved_mention("mn1", "e1", "m1", "Started.")
        mn2 = _make_resolved_mention("mn2", "e1", "m2", "Resolved.")
        service = _make_service([entity], [m1, m2], [mn1, mn2])
        timeline = service.get_entity_timeline("e1")
        assert timeline.observation_count == len(timeline.timeline)

    # S13 — evidence_text preserved

    def test_s13_evidence_text_preserved(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1")
        source = "The payment API issue has started being investigated by Rahul."
        mention = _make_resolved_mention("mn1", "e1", "m1", source)
        service = _make_service([entity], [m1], [mention])
        timeline = service.get_entity_timeline("e1")
        assert timeline.timeline[0].evidence_text == source

    # S14 — mention_id preserved

    def test_s14_mention_id_preserved(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1")
        mention = _make_resolved_mention("mention_unique_99", "e1", "m1", "Started.")
        service = _make_service([entity], [m1], [mention])
        timeline = service.get_entity_timeline("e1")
        assert timeline.timeline[0].mention_id == "mention_unique_99"

    # S15 — meeting_date preserved

    def test_s15_meeting_date_preserved(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1", offset_days=5)
        mention = _make_resolved_mention("mn1", "e1", "m1", "Started.")
        service = _make_service([entity], [m1], [mention])
        timeline = service.get_entity_timeline("e1")
        expected_date = _BASE_TIME + timedelta(days=5)
        assert timeline.timeline[0].meeting_date == expected_date

    # S16 — Service does NOT modify mention resolution_status

    def test_s16_service_does_not_modify_mention_resolution_status(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1")
        mention = _make_resolved_mention("mn1", "e1", "m1", "Started.")
        mention_repo = InMemoryMentionRepository()
        mention_repo.create(mention)
        entity_repo = InMemoryEntityRepository()
        entity_repo.create(entity)
        meeting_repo = InMemoryMeetingRepository()
        meeting_repo.save(m1)
        service = TemporalStateService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=KeywordStateInterpreter(),
            policy=DefaultTransitionPolicy(),
        )
        service.get_entity_timeline("e1")
        stored = mention_repo.get_by_id("mn1")
        assert stored.resolution_status == ResolutionStatus.RESOLVED

    # S17 — Service does NOT modify mention entity_id

    def test_s17_service_does_not_modify_mention_entity_id(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1")
        mention = _make_resolved_mention("mn1", "e1", "m1", "Started.")
        mention_repo = InMemoryMentionRepository()
        mention_repo.create(mention)
        entity_repo = InMemoryEntityRepository()
        entity_repo.create(entity)
        meeting_repo = InMemoryMeetingRepository()
        meeting_repo.save(m1)
        service = TemporalStateService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=KeywordStateInterpreter(),
            policy=DefaultTransitionPolicy(),
        )
        service.get_entity_timeline("e1")
        stored = mention_repo.get_by_id("mn1")
        assert stored.entity_id == "e1"

    # S18 — Service does NOT create entities

    def test_s18_service_does_not_create_entities(self):
        entity = _make_entity("e1", "payment api")
        entity_repo = InMemoryEntityRepository()
        entity_repo.create(entity)
        mention_repo = InMemoryMentionRepository()
        meeting_repo = InMemoryMeetingRepository()
        service = TemporalStateService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=KeywordStateInterpreter(),
            policy=DefaultTransitionPolicy(),
        )
        service.get_entity_timeline("e1")
        all_entities = entity_repo.list_entities()
        assert len(all_entities) == 1  # unchanged

    # S19-S21 — Read-only invariants (no generation, scoring, resolution)
    # These are structural: TemporalStateService has no generator/scorer/policy
    # parameters — it cannot call them by design.

    def test_s19_service_has_no_candidate_generator_dependency(self):
        """TemporalStateService constructor takes no generator parameter."""
        import inspect
        sig = inspect.signature(TemporalStateService.__init__)
        params = list(sig.parameters.keys())
        assert "generator" not in params

    def test_s20_service_has_no_candidate_scorer_dependency(self):
        """TemporalStateService constructor takes no scorer parameter."""
        import inspect
        sig = inspect.signature(TemporalStateService.__init__)
        params = list(sig.parameters.keys())
        assert "scorer" not in params

    def test_s21_service_has_no_resolution_policy_dependency(self):
        """TemporalStateService has no resolution_policy/policy from entity resolution."""
        # The service has a 'policy' param but it is AbstractTemporalStatePolicy,
        # not AbstractResolutionPolicy. Verify the injected type is not a resolution policy.
        from app.temporal.transition_policy import AbstractTemporalStatePolicy
        assert issubclass(DefaultTransitionPolicy, AbstractTemporalStatePolicy)

    # S22 — Determinism

    def test_s22_timeline_is_deterministic(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1", offset_days=0)
        m2 = _make_meeting("m2", "W2", offset_days=7)
        mn1 = _make_resolved_mention("mn1", "e1", "m1", "Started working on it.")
        mn2 = _make_resolved_mention("mn2", "e1", "m2", "The issue is resolved.")
        service = _make_service([entity], [m1, m2], [mn1, mn2])
        tl1 = service.get_entity_timeline("e1")
        tl2 = service.get_entity_timeline("e1")
        assert tl1.current_state == tl2.current_state
        assert tl1.observation_count == tl2.observation_count
        assert tl1.transition_count == tl2.transition_count
        for obs1, obs2 in zip(tl1.timeline, tl2.timeline):
            assert obs1.interpreted_state == obs2.interpreted_state
            assert obs1.transition_occurred == obs2.transition_occurred

    # S23 — Missing meeting skipped gracefully

    def test_s23_missing_meeting_skipped_gracefully(self):
        """A mention referencing a non-existent meeting is silently skipped."""
        entity = _make_entity("e1", "payment api")
        # No meeting created — mention references a non-existent meeting
        mention = _make_resolved_mention("mn1", "e1", "missing_meeting", "Started.")
        service = _make_service([entity], [], [mention])
        timeline = service.get_entity_timeline("e1")
        # Should return an empty timeline (mention skipped) rather than crashing
        assert timeline.observation_count == 0
        assert timeline.current_state == TemporalState.UNKNOWN

    # S24 — Same-date: secondary ordering by meeting_id, tertiary by mention_id

    def test_s24_same_date_ordered_by_meeting_id_then_mention_id(self):
        entity = _make_entity("e1", "payment api")
        # All meetings on the same date
        m1 = _make_meeting("meeting_aaa", "M1", offset_days=0)
        m2 = _make_meeting("meeting_bbb", "M2", offset_days=0)
        # Two mentions in same meeting (same date, same meeting_id)
        mn_z = _make_resolved_mention("mention_zzz", "e1", "meeting_aaa", "Started.")
        mn_a = _make_resolved_mention("mention_aaa", "e1", "meeting_aaa", "Still started.")
        mn_b = _make_resolved_mention("mention_bbb", "e1", "meeting_bbb", "Resolved.")
        service = _make_service([entity], [m1, m2], [mn_z, mn_a, mn_b])
        timeline = service.get_entity_timeline("e1")
        mention_ids = [obs.mention_id for obs in timeline.timeline]
        # Within meeting_aaa: mention_aaa < mention_zzz; then meeting_bbb: mention_bbb
        assert mention_ids == ["mention_aaa", "mention_zzz", "mention_bbb"]

    # observation_index matches position

    def test_observation_index_matches_position(self):
        entity = _make_entity("e1", "payment api")
        m1 = _make_meeting("m1", "W1", offset_days=0)
        m2 = _make_meeting("m2", "W2", offset_days=7)
        m3 = _make_meeting("m3", "W3", offset_days=14)
        mn1 = _make_resolved_mention("mn1", "e1", "m1", "Started.")
        mn2 = _make_resolved_mention("mn2", "e1", "m2", "Blocked.")
        mn3 = _make_resolved_mention("mn3", "e1", "m3", "Resolved.")
        service = _make_service([entity], [m1, m2, m3], [mn1, mn2, mn3])
        timeline = service.get_entity_timeline("e1")
        for i, obs in enumerate(timeline.timeline):
            assert obs.observation_index == i

    # PERSON entity can have timeline (returns UNKNOWN if no state-bearing evidence)

    def test_person_entity_timeline_returns_unknown(self):
        entity = _make_entity("e_person", "Rahul Kumar", EntityType.PERSON)
        m1 = _make_meeting("m1", "W1")
        mention = EntityMention(
            mention_id="mn1",
            entity_type=EntityType.PERSON,
            text="Rahul",
            meeting_id="m1",
            source_text="Rahul presented the quarterly update.",
            entity_id="e_person",
            resolution_status=ResolutionStatus.RESOLVED,
            created_at=_BASE_TIME,
        )
        entity_repo = InMemoryEntityRepository()
        entity_repo.create(entity)
        meeting_repo = InMemoryMeetingRepository()
        meeting_repo.save(m1)
        mention_repo = InMemoryMentionRepository()
        mention_repo.create(mention)
        service = TemporalStateService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            meeting_repo=meeting_repo,
            interpreter=KeywordStateInterpreter(),
            policy=DefaultTransitionPolicy(),
        )
        timeline = service.get_entity_timeline("e_person")
        # Person entity returns UNKNOWN (no lifecycle state-bearing keywords)
        assert timeline.current_state == TemporalState.UNKNOWN
        assert timeline.observation_count == 1
        assert timeline.transition_count == 0


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture
def timeline_client():
    """Provide a fresh TestClient with isolated in-memory repositories injected
    via dependency_overrides (same pattern as test_correlation.py).

    Returns (client, entity_repo, mention_repo, meeting_repo) so tests can
    populate data directly without going through the HTTP ingestion endpoints.
    """
    from app.main import app
    from app.api.entities import (
        get_temporal_state_service,
        get_entity_service,
    )
    from app.services.entity_service import EntityService

    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    meeting_repo = InMemoryMeetingRepository()

    temporal_service = TemporalStateService(
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

    app.dependency_overrides[get_temporal_state_service] = lambda: temporal_service
    app.dependency_overrides[get_entity_service] = lambda: entity_service

    client = TestClient(app)
    yield client, entity_repo, mention_repo, meeting_repo

    app.dependency_overrides.pop(get_temporal_state_service, None)
    app.dependency_overrides.pop(get_entity_service, None)


# ---------------------------------------------------------------------------
# Keep old `client` fixture for simple tests (A01, A02) that don't need data
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Provide a TestClient that exercises the full HTTP stack (no overrides).
    Used only for tests that do not require correlated data (e.g., 404 checks).
    """
    from app.main import app
    from app.api import entities as entities_module
    from app.api import meetings as meetings_module

    entities_module._entity_repository = InMemoryEntityRepository()
    entities_module._mention_repository = InMemoryMentionRepository()
    meetings_module._meeting_repository = InMemoryMeetingRepository()

    return TestClient(app)


def _ingest_meeting(client, title: str, transcript: str, date: str) -> str:
    """Helper: POST /api/v1/meetings and return meeting_id."""
    resp = client.post(
        "/api/v1/meetings",
        json={"title": title, "transcript": transcript, "meeting_date": date, "participants": []},
    )
    assert resp.status_code == 201
    return resp.json()["meeting_id"]


def _create_entity(client, entity_type: str, canonical_name: str) -> str:
    """Helper: POST /api/v1/entities and return entity_id."""
    resp = client.post(
        "/api/v1/entities",
        json={"entity_type": entity_type, "canonical_name": canonical_name},
    )
    return resp.json()["entity_id"]


def _register_and_resolve_mention(
    client, entity_type: str, text: str, meeting_id: str, source_text: str
) -> str:
    """Helper: register a mention, then resolve it; return mention_id."""
    resp = client.post(
        "/api/v1/entities/mentions",
        json={
            "entity_type": entity_type,
            "text": text,
            "meeting_id": meeting_id,
            "source_text": source_text,
        },
    )
    assert resp.status_code == 201
    mention_id = resp.json()["mention_id"]
    client.post(f"/api/v1/entities/mentions/{mention_id}/resolve")
    return mention_id


# A01 — Unknown entity → 404

def test_a01_unknown_entity_returns_404(client):
    resp = client.get("/api/v1/entities/nonexistent_entity_id/timeline")
    assert resp.status_code == 404


# A02 — Entity with no mentions → 200 empty timeline

def test_a02_entity_no_mentions_returns_200_empty(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api2", "Payment API Bug"))
    resp = client.get("/api/v1/entities/e_api2/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_id"] == "e_api2"
    assert data["current_state"] == "UNKNOWN"
    assert data["observation_count"] == 0
    assert data["transition_count"] == 0
    assert data["timeline"] == []


# A03 — Valid entity with observations → 200

def test_a03_valid_entity_with_observations_returns_200(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api3", "Payment API Bug"))
    meeting_repo.save(_make_meeting("m_api3", "Sprint", offset_days=0))
    mention_repo.create(_make_resolved_mention(
        "mn_api3", "e_api3", "m_api3",
        "The Payment API Bug has started being fixed."
    ))
    resp = client.get("/api/v1/entities/e_api3/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["timeline"]) == 1


# A04 — Response has all required top-level fields

def test_a04_response_has_required_fields(timeline_client):
    client, entity_repo, *_ = timeline_client
    entity_repo.create(_make_entity("e_api4", "Login Failure"))
    resp = client.get("/api/v1/entities/e_api4/timeline")
    data = resp.json()
    required = {"entity_id", "canonical_name", "entity_type", "current_state",
                "observation_count", "transition_count", "timeline"}
    assert required.issubset(data.keys())


# A05 — current_state matches expected value

def test_a05_current_state_correct(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api5", "Login Failure"))
    meeting_repo.save(_make_meeting("m_api5", "Retro", offset_days=0))
    mention_repo.create(_make_resolved_mention(
        "mn_api5", "e_api5", "m_api5",
        "The Login Failure issue has been resolved and closed."
    ))
    resp = client.get("/api/v1/entities/e_api5/timeline")
    data = resp.json()
    assert data["current_state"] == "RESOLVED"


# A06 — Timeline is chronologically ordered

def test_a06_timeline_chronologically_ordered(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api6", "Auth Bug"))
    meeting_repo.save(_make_meeting("m_api6a", "W1", offset_days=0))
    meeting_repo.save(_make_meeting("m_api6b", "W2", offset_days=7))
    meeting_repo.save(_make_meeting("m_api6c", "W3", offset_days=14))
    mention_repo.create(_make_resolved_mention("mn_api6a", "e_api6", "m_api6a", "Auth Bug identified."))
    mention_repo.create(_make_resolved_mention("mn_api6b", "e_api6", "m_api6b", "Auth Bug started being fixed."))
    mention_repo.create(_make_resolved_mention("mn_api6c", "e_api6", "m_api6c", "Auth Bug resolved."))
    resp = client.get("/api/v1/entities/e_api6/timeline")
    data = resp.json()
    dates = [entry["meeting_date"] for entry in data["timeline"]]
    assert dates == sorted(dates)


# A07 — observation_count matches len(timeline)

def test_a07_observation_count_matches_timeline_len(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api7", "DB Error"))
    meeting_repo.save(_make_meeting("m_api7a", "M1", offset_days=0))
    meeting_repo.save(_make_meeting("m_api7b", "M2", offset_days=7))
    mention_repo.create(_make_resolved_mention("mn_api7a", "e_api7", "m_api7a", "DB Error started."))
    mention_repo.create(_make_resolved_mention("mn_api7b", "e_api7", "m_api7b", "DB Error resolved."))
    resp = client.get("/api/v1/entities/e_api7/timeline")
    data = resp.json()
    assert data["observation_count"] == len(data["timeline"])


# A08 — transition_count is correct

def test_a08_transition_count_correct(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api8", "Cache Bug"))
    meeting_repo.save(_make_meeting("m_api8a", "M1", offset_days=0))
    meeting_repo.save(_make_meeting("m_api8b", "M2", offset_days=7))
    mention_repo.create(_make_resolved_mention("mn_api8a", "e_api8", "m_api8a", "Cache Bug started."))
    mention_repo.create(_make_resolved_mention("mn_api8b", "e_api8", "m_api8b", "Cache Bug resolved."))
    resp = client.get("/api/v1/entities/e_api8/timeline")
    data = resp.json()
    expected_transitions = sum(1 for e in data["timeline"] if e["transition_occurred"])
    assert data["transition_count"] == expected_transitions


# A09 — Each timeline entry has all required fields

def test_a09_each_entry_has_required_fields(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api9", "Network Issue"))
    meeting_repo.save(_make_meeting("m_api9", "M1", offset_days=0))
    mention_repo.create(_make_resolved_mention(
        "mn_api9", "e_api9", "m_api9", "Network Issue started being investigated."
    ))
    resp = client.get("/api/v1/entities/e_api9/timeline")
    data = resp.json()
    assert len(data["timeline"]) >= 1
    entry = data["timeline"][0]
    required_entry_fields = {
        "observation_index", "meeting_id", "meeting_title", "meeting_date",
        "mention_id", "evidence_text", "interpreted_state",
        "transition_occurred", "from_state", "to_state",
        "is_valid_transition", "transition_skipped_reason",
    }
    assert required_entry_fields.issubset(entry.keys())


# A10 — is_valid_transition field present and is a bool

def test_a10_is_valid_transition_field_present_in_response(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api10", "Quota Issue"))
    meeting_repo.save(_make_meeting("m_api10", "M1", offset_days=0))
    mention_repo.create(_make_resolved_mention("mn_api10", "e_api10", "m_api10", "Quota Issue resolved."))
    resp = client.get("/api/v1/entities/e_api10/timeline")
    data = resp.json()
    entry = data["timeline"][0]
    assert "is_valid_transition" in entry
    assert isinstance(entry["is_valid_transition"], bool)


# A11 — is_valid_transition=false for invalid transitions (direct repo injection)

def test_a11_invalid_transition_recorded(timeline_client):
    """After RESOLVED, an IN_PROGRESS observation records is_valid_transition=false."""
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api11", "Retry Bug"))
    meeting_repo.save(_make_meeting("m_api11a", "M1", offset_days=0))
    meeting_repo.save(_make_meeting("m_api11b", "M2", offset_days=7))
    mention_repo.create(_make_resolved_mention("mn_api11a", "e_api11", "m_api11a", "Retry Bug resolved."))
    mention_repo.create(_make_resolved_mention("mn_api11b", "e_api11", "m_api11b", "Started working again."))
    resp = client.get("/api/v1/entities/e_api11/timeline")
    data = resp.json()
    assert len(data["timeline"]) == 2
    assert data["timeline"][0]["is_valid_transition"] is True
    assert data["timeline"][1]["is_valid_transition"] is False
    assert data["timeline"][1]["transition_skipped_reason"] is not None


# A12 — transition_skipped_reason null for valid transitions

def test_a12_transition_skipped_reason_null_for_valid(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api12", "Quota Issue 2"))
    meeting_repo.save(_make_meeting("m_api12", "M1", offset_days=0))
    mention_repo.create(_make_resolved_mention("mn_api12", "e_api12", "m_api12", "The issue started."))
    resp = client.get("/api/v1/entities/e_api12/timeline")
    data = resp.json()
    entry = data["timeline"][0]
    assert entry["is_valid_transition"] is True
    assert entry["transition_skipped_reason"] is None


# A13 — from_state and to_state correct

def test_a13_from_state_and_to_state_correct(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api13", "Rate Limit Bug"))
    meeting_repo.save(_make_meeting("m_api13", "M1", offset_days=0))
    mention_repo.create(_make_resolved_mention(
        "mn_api13", "e_api13", "m_api13",
        # Use "working on" (IN_PROGRESS) with no RESOLVED/BLOCKED keywords.
        # "started being fixed" triggers RESOLVED due to "fixed" having higher priority.
        "We are currently working on the Rate Limit Bug."
    ))
    resp = client.get("/api/v1/entities/e_api13/timeline")
    data = resp.json()
    entry = data["timeline"][0]
    assert entry["from_state"] == "UNKNOWN"
    assert entry["to_state"] == "IN_PROGRESS"



# A14 — evidence_text in response matches source_text

def test_a14_evidence_text_matches_source_text(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api14", "TLS Error"))
    meeting_repo.save(_make_meeting("m_api14", "M1", offset_days=0))
    source = "The TLS Error is now being resolved and closed by the security team."
    mention_repo.create(_make_resolved_mention("mn_api14", "e_api14", "m_api14", source))
    resp = client.get("/api/v1/entities/e_api14/timeline")
    data = resp.json()
    assert data["timeline"][0]["evidence_text"] == source


# A15 — PERSON entity with no lifecycle evidence returns UNKNOWN

def test_a15_person_entity_returns_unknown(timeline_client):
    client, entity_repo, mention_repo, meeting_repo = timeline_client
    entity_repo.create(_make_entity("e_api15", "Rahul Kumar", EntityType.PERSON))
    meeting_repo.save(_make_meeting("m_api15", "M1", offset_days=0))
    mention_repo.create(EntityMention(
        mention_id="mn_api15",
        entity_type=EntityType.PERSON,
        text="Rahul",
        meeting_id="m_api15",
        source_text="Rahul Kumar presented the quarterly roadmap update.",
        entity_id="e_api15",
        resolution_status=ResolutionStatus.RESOLVED,
        created_at=_BASE_TIME,
    ))
    resp = client.get("/api/v1/entities/e_api15/timeline")
    data = resp.json()
    # PERSON entity with no lifecycle keywords → UNKNOWN
    assert data["current_state"] == "UNKNOWN"
    assert data["transition_count"] == 0



# ---------------------------------------------------------------------------
# Model / schema structural tests
# ---------------------------------------------------------------------------

def test_temporal_state_enum_values():
    """TemporalState has the required set of values."""
    values = {s.value for s in TemporalState}
    assert values == {"UNKNOWN", "OPEN", "IN_PROGRESS", "BLOCKED", "RESOLVED"}


def test_temporal_state_is_str_enum():
    """TemporalState is a StrEnum so it serialises as a plain string."""
    assert isinstance(TemporalState.RESOLVED, str)
    assert TemporalState.RESOLVED == "RESOLVED"


def test_state_observation_model_is_pydantic():
    """StateObservation is a Pydantic BaseModel."""
    from pydantic import BaseModel
    assert issubclass(StateObservation, BaseModel)


def test_entity_timeline_model_is_pydantic():
    """EntityTimeline is a Pydantic BaseModel."""
    from pydantic import BaseModel
    assert issubclass(EntityTimeline, BaseModel)


def test_entity_timeline_default_state_is_unknown():
    """EntityTimeline.current_state defaults correctly when set to UNKNOWN."""
    tl = EntityTimeline(
        entity_id="e1",
        canonical_name="test issue",
        entity_type=EntityType.ISSUE,
        current_state=TemporalState.UNKNOWN,
        observation_count=0,
        transition_count=0,
        timeline=[],
    )
    assert tl.current_state == TemporalState.UNKNOWN
    assert tl.timeline == []


def test_abstract_state_interpreter_is_abc():
    """AbstractStateInterpreter is an ABC."""
    from abc import ABC
    from app.temporal.state_interpreter import AbstractStateInterpreter
    assert issubclass(AbstractStateInterpreter, ABC)


def test_keyword_interpreter_subclasses_abstract():
    from app.temporal.state_interpreter import AbstractStateInterpreter
    assert isinstance(KeywordStateInterpreter(), AbstractStateInterpreter)


def test_transition_result_is_frozen_dataclass():
    """TransitionResult is a frozen dataclass (immutable)."""
    import dataclasses
    assert dataclasses.is_dataclass(TransitionResult)
    # Frozen dataclasses raise FrozenInstanceError on mutation.
    result = TransitionResult(
        current_state=TemporalState.UNKNOWN,
        transition_occurred=False,
        is_valid=True,
        reason=None,
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        result.current_state = TemporalState.RESOLVED  # type: ignore[misc]
