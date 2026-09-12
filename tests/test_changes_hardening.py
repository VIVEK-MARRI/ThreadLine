"""Stage 17.1 Hardening Tests for Organisation-Wide Change Intelligence Engine."""

from datetime import datetime, timezone, timedelta

from app.models.entity import CanonicalEntity, EntityType, ResolutionStatus, EntityMention
from app.models.meeting import Meeting
from app.models.relationships import RelationshipType
from app.models.organisation_change import OrganisationChangeType
from tests.test_changes import (
    _BASE_TIME,
    _RECENT,
    _make_entity,
    _make_meeting,
    _make_mention,
    _make_dep,
    _build_service,
)


def test_h01_new_dependency_uses_meeting_date() -> None:
    """NEW_DEPENDENCY detected_at should use actual meeting_date if found."""
    e1 = _make_entity("e1", "A")
    e2 = _make_entity("e2", "B")
    meeting_time = _BASE_TIME - timedelta(days=2)
    mtg = _make_meeting("m1", meeting_time)
    mn1 = _make_mention("mn1", "e1", "m1", "A")
    mn2 = _make_mention("mn2", "e2", "m1", "B")
    dep = _make_dep("d1", "e1", "e2", RelationshipType.DEPENDS_ON, "m1", "mn1", "A -> B")

    service = _build_service([e1, e2], [mtg], [mn1, mn2], [dep])
    changes = service.get_changes(current_time=_BASE_TIME)

    new_deps = [c for c in changes if c.change_type == OrganisationChangeType.NEW_DEPENDENCY]
    assert len(new_deps) == 1
    assert new_deps[0].detected_at == meeting_time


def test_h02_new_dependency_missing_meeting_fallback() -> None:
    """NEW_DEPENDENCY is skipped if temporal evidence (meeting) cannot be established."""
    e1 = _make_entity("e1", "A")
    e2 = _make_entity("e2", "B")
    # Provide the mention and dep, but NO MEETING in the repo.
    mn1 = _make_mention("mn1", "e1", "m1", "A")
    dep = _make_dep("d1", "e1", "e2", RelationshipType.DEPENDS_ON, "m1", "mn1", "A -> B")

    service = _build_service([e1, e2], [], [mn1], [dep])
    changes = service.get_changes(current_time=_BASE_TIME)

    new_deps = [c for c in changes if c.change_type == OrganisationChangeType.NEW_DEPENDENCY]
    assert len(new_deps) == 0


def test_h03_two_calls_different_current_time_identical_results() -> None:
    """Changing current_time does not alter historical detected_at timestamps."""
    e1 = _make_entity("e1", "A")
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "A raised")  # OPEN

    service = _build_service([e1], [mtg], [mn1])
    # Call with two different current times. Both should report detected_at = _RECENT.
    c1 = service.get_changes(current_time=_BASE_TIME)
    c2 = service.get_changes(current_time=_BASE_TIME + timedelta(days=10))

    assert c1[0].detected_at == _RECENT
    assert c2[0].detected_at == _RECENT
    assert c1[0].change_id == c2[0].change_id


def test_h04_impact_expanded_change_id_stable() -> None:
    """IMPACT_EXPANDED change_id should be stable for the same impact sources."""
    e1 = _make_entity("e1", "target")
    e2 = _make_entity("e2", "source1")
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "target")
    mn2 = _make_mention("mn2", "e2", "m1", "source1 is blocked")
    dep = _make_dep("d1", "e1", "e2", RelationshipType.DEPENDS_ON, "m1", "mn1", "target deps source1")

    service = _build_service([e1, e2], [mtg], [mn1, mn2], [dep])
    c1 = service.get_changes(current_time=_BASE_TIME)
    imp1 = [c for c in c1 if c.change_type == OrganisationChangeType.IMPACT_EXPANDED]
    assert len(imp1) == 1
    id1 = imp1[0].change_id
    assert imp1[0].detected_at is None

    # Evaluate again with same data, should yield same ID.
    c2 = service.get_changes(current_time=_BASE_TIME)
    imp2 = [c for c in c2 if c.change_type == OrganisationChangeType.IMPACT_EXPANDED]
    assert imp2[0].change_id == id1


def test_h05_impact_expanded_change_id_changes_with_new_source() -> None:
    """IMPACT_EXPANDED change_id differs if the set of sources changes."""
    e1 = _make_entity("e1", "target")
    e2 = _make_entity("e2", "source1")
    e3 = _make_entity("e3", "source2")
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "target")
    mn2 = _make_mention("mn2", "e2", "m1", "source1 is blocked")
    dep1 = _make_dep("d1", "e1", "e2", RelationshipType.DEPENDS_ON, "m1", "mn1", "target deps source1")

    service1 = _build_service([e1, e2], [mtg], [mn1, mn2], [dep1])
    c1 = service1.get_changes(current_time=_BASE_TIME)
    id1 = [c.change_id for c in c1 if c.change_type == OrganisationChangeType.IMPACT_EXPANDED][0]

    # Add second source blocked
    mn3 = _make_mention("mn3", "e3", "m1", "source2 is blocked")
    dep2 = _make_dep("d2", "e1", "e3", RelationshipType.DEPENDS_ON, "m1", "mn1", "target deps source2")
    service2 = _build_service([e1, e2, e3], [mtg], [mn1, mn2, mn3], [dep1, dep2])
    c2 = service2.get_changes(current_time=_BASE_TIME)
    id2 = [c.change_id for c in c2 if c.change_type == OrganisationChangeType.IMPACT_EXPANDED][0]

    assert id1 != id2


def test_h06_semantic_evidence_strings() -> None:
    """Verify conservative wording is used in evidence strings."""
    e1 = _make_entity("e1", "A")
    e2 = _make_entity("e2", "B")
    e3 = _make_entity("e3", "C")
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "A is blocked")
    mn2 = _make_mention("mn2", "e2", "m1", "B")
    mn3 = _make_mention("mn3", "e3", "m1", "C is resolved")
    dep = _make_dep("d1", "e2", "e1", RelationshipType.DEPENDS_ON, "m1", "mn2", "B deps A")

    service = _build_service([e1, e2, e3], [mtg], [mn1, mn2, mn3], [dep])
    changes = service.get_changes(current_time=_BASE_TIME)

    # Check RISK_ESCALATED on e1 (blocked)
    esc = [c for c in changes if c.change_type == OrganisationChangeType.RISK_ESCALATED]
    if esc:
        assert "current-state signal" in esc[0].evidence.lower()
        assert "not a proven historical risk" in esc[0].evidence.lower()

    # Check RISK_DEESCALATED on e3
    deesc = [c for c in changes if c.change_type == OrganisationChangeType.RISK_DEESCALATED]
    if deesc:
        assert "policy" in deesc[0].evidence.lower()

    # Check IMPACT_EXPANDED on e2 (impacted by e1)
    imp = [c for c in changes if c.change_type == OrganisationChangeType.IMPACT_EXPANDED]
    if imp:
        assert "associations observed" in imp[0].evidence.lower()
        assert "not that they grew" in imp[0].evidence.lower()


def test_h07_failure_isolation() -> None:
    """If one entity fails processing, it should not break others."""
    e1 = _make_entity("e1", "valid")
    e2 = _make_entity("e2", "fails")

    from app.repositories.dependency_repository import InMemoryDependencyRepository
    class FailingDependencyRepo(InMemoryDependencyRepository):
        def list_by_source_entity_id(self, entity_id: str):
            if entity_id == "e2":
                raise ValueError("Simulated failure")
            return super().list_by_source_entity_id(entity_id)

    from app.repositories.entity_repository import InMemoryEntityRepository
    e_repo = InMemoryEntityRepository()
    e_repo.create(e1)
    e_repo.create(e2)
    dep_repo = FailingDependencyRepo()

    from app.services.organisation_change_intelligence_service import OrganisationChangeIntelligenceService
    from app.temporal.state_interpreter import KeywordStateInterpreter
    from app.temporal.transition_policy import DefaultTransitionPolicy
    from app.repositories.meeting_repository import InMemoryMeetingRepository
    from app.repositories.mention_repository import InMemoryMentionRepository

    mention_repo = InMemoryMentionRepository()
    meeting_repo = InMemoryMeetingRepository()

    service = OrganisationChangeIntelligenceService(
        entity_repo=e_repo,
        mention_repo=mention_repo,
        meeting_repo=meeting_repo,
        interpreter=KeywordStateInterpreter(),
        policy=DefaultTransitionPolicy(),
        dependency_repo=dep_repo,
    )

    # Provide a simple valid change for e1
    mtg = _make_meeting("m1", _RECENT)
    mn1 = _make_mention("mn1", "e1", "m1", "valid is blocked")
    meeting_repo.save(mtg)
    mention_repo.create(mn1)

    changes = service.get_changes(current_time=_BASE_TIME)
    # e2 raised an exception and was skipped, but e1 should still produce its changes (STATE_BLOCKED, etc)
    assert any(c.entity_id == "e1" for c in changes)
    assert all(c.entity_id != "e2" for c in changes)
