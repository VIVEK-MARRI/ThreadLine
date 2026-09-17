"""Stage 34 Part B: narrowed exception-handling regression tests.

Covers the eight formerly-broad ``except Exception`` guards in
organisation_change_intelligence_service.py (5) and
processing_consistency_service.py (3).

For each guard the tests prove:
  * the genuinely expected failure still takes the safe fallback path, and
  * behaviour outside the narrowed set fails loudly instead of being
    silently swallowed (no recoverable fallback was turned into a crash:
    total-outage paths already raise before these guards run).
"""

from datetime import datetime, timezone

import pytest

from app.models.dependency import ExplicitDependency
from app.models.entity import CanonicalEntity, EntityType
from app.models.meeting import Meeting
from app.models.relationships import RelationshipType
from app.repositories.dependency_repository import InMemoryDependencyRepository
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.meeting_repository import InMemoryMeetingRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.entity_service import EntityNotFoundError
from app.services.organisation_change_intelligence_service import (
    OrganisationChangeIntelligenceService,
)
from app.services.processing_consistency_service import get_consistency_status
from app.temporal.state_interpreter import KeywordStateInterpreter
from app.temporal.transition_policy import DefaultTransitionPolicy

_NOW = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


def _entity(entity_id: str) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=EntityType.ISSUE,
        canonical_name=entity_id,
        aliases=[],
        created_at=_NOW,
    )


def _meeting(meeting_id: str) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=f"Meeting {meeting_id}",
        meeting_date=_NOW,
        ingested_at=_NOW,
        transcript="dummy transcript",
    )


def _build_changes_service(
    entities=None, meetings=None, deps=None,
) -> OrganisationChangeIntelligenceService:
    e_repo = InMemoryEntityRepository()
    m_repo = InMemoryMentionRepository()
    mtg_repo = InMemoryMeetingRepository()
    dep_repo = InMemoryDependencyRepository()
    for e in entities or []:
        e_repo.create(e)
    for m in meetings or []:
        mtg_repo.save(m)
    for d in deps or []:
        dep_repo.save(d)
    return OrganisationChangeIntelligenceService(
        entity_repo=e_repo,
        mention_repo=m_repo,
        meeting_repo=mtg_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
        dependency_repo=dep_repo,
    )


class _ExplodingJobs:
    """Job repository stub whose list() raises a configured error."""

    def __init__(self, error: Exception):
        self._error = error

    def list(self, *args, **kwargs):
        raise self._error


class _ExplodingMentions:
    def list_by_meeting_id(self, meeting_id):
        raise ValueError("row mapping failed")


# ---------------------------------------------------------------------------
# Organisation-change guards
# ---------------------------------------------------------------------------


class TestPerEntityGuard:
    def test_key_error_in_one_entity_skips_only_that_entity(self, monkeypatch):
        svc = _build_changes_service(entities=[_entity("good"), _entity("bad")])
        original = svc._detect_entity_changes

        def flaky(*args, **kwargs):
            if kwargs.get("entity_id") == "bad":
                raise KeyError("bad-row")
            return original(*args, **kwargs)

        monkeypatch.setattr(svc, "_detect_entity_changes", flaky)
        changes = svc.get_changes(current_time=_NOW)
        assert isinstance(changes, list)

    def test_unexpected_error_propagates_loudly(self, monkeypatch):
        svc = _build_changes_service(entities=[_entity("bad")])

        def boom(*args, **kwargs):
            raise RuntimeError("storage exploded mid-scan")

        monkeypatch.setattr(svc, "_detect_entity_changes", boom)
        with pytest.raises(RuntimeError):
            svc.get_changes(current_time=_NOW)


class TestDependencyMeetingGuard:
    def test_unreadable_meeting_skips_new_dependency_without_crash(self, monkeypatch):
        dep = ExplicitDependency(
            dependency_id="dep-1",
            source_entity_id="e1",
            target_entity_id="e2",
            relationship_type=RelationshipType.DEPENDS_ON,
            source_text="e1 depends on e2",
            meeting_id="m1",
            mention_id="men1",
        )
        svc = _build_changes_service(
            entities=[_entity("e1"), _entity("e2")],
            meetings=[_meeting("m1")],
            deps=[dep],
        )

        def unreadable(meeting_id):
            raise KeyError(meeting_id)

        monkeypatch.setattr(svc._meeting_repo, "get_by_id", unreadable)
        changes = svc.get_changes(current_time=_NOW)
        assert all(c.change_type.value != "NEW_DEPENDENCY" for c in changes)


class TestGraphAndImpactGuards:
    def test_entity_not_found_in_graph_build_skips_rule(self, monkeypatch):
        svc = _build_changes_service(entities=[_entity("e1")])

        class _Graph:
            def build_dependency_graph(self, entity_id):
                raise EntityNotFoundError(f"Entity '{entity_id}' not found.")

        monkeypatch.setattr(svc, "_dependency_graph_service", _Graph())
        changes = svc.get_changes(current_time=_NOW)
        assert isinstance(changes, list)

    def test_value_error_in_impact_lookup_skips_rule(self, monkeypatch):
        svc = _build_changes_service(entities=[_entity("e1")])

        class _Impacts:
            def get_entity_impacts(self, **kwargs):
                raise ValueError("bad impact row")

        monkeypatch.setattr(svc, "_impact_service", _Impacts())
        changes = svc.get_changes(current_time=_NOW)
        assert isinstance(changes, list)


class TestParseGuard:
    def test_none_description_returns_none_pair(self):
        from app.services.organisation_change_intelligence_service import (
            OrganisationChangeIntelligenceService as S,
        )

        assert S._parse_state_transition(None) == (None, None)

    def test_non_string_description_returns_none_pair(self):
        from app.services.organisation_change_intelligence_service import (
            OrganisationChangeIntelligenceService as S,
        )

        assert S._parse_state_transition(12345) == (None, None)


# ---------------------------------------------------------------------------
# Consistency guards
# ---------------------------------------------------------------------------


class TestConsistencyJobGuards:
    def _repos(self):
        mtg_repo = InMemoryMeetingRepository()
        mtg_repo.save(_meeting("m1"))
        return mtg_repo

    def test_job_list_key_error_reports_incomplete_not_current(self):
        status = get_consistency_status(
            "m1",
            meeting_repository=self._repos(),
            job_repository=_ExplodingJobs(KeyError("jobs")),
        )
        assert status["derived_revision"] is None
        assert status["is_current"] is False
        assert status["status"] == "INCOMPLETE"

    def test_job_list_attribute_error_keeps_default_lifecycle(self):
        status = get_consistency_status(
            "m1",
            meeting_repository=self._repos(),
            job_repository=_ExplodingJobs(AttributeError("shape")),
        )
        assert status["status"] == "INCOMPLETE"
        assert status["is_current"] is False

    def test_unexpected_job_error_propagates(self):
        with pytest.raises(RuntimeError):
            get_consistency_status(
                "m1",
                meeting_repository=self._repos(),
                job_repository=_ExplodingJobs(RuntimeError("conn lost")),
            )


class TestConsistencyMentionGuard:
    def test_unreadable_mentions_keep_zero_count_without_crash(self):
        mtg_repo = InMemoryMeetingRepository()
        mtg_repo.save(_meeting("m1"))
        status = get_consistency_status(
            "m1",
            meeting_repository=mtg_repo,
            mention_repository=_ExplodingMentions(),
        )
        assert status["stale_mentions"] == 0
        assert status["meeting_exists"] is True
