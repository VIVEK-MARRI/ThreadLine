"""Comprehensive unit tests for DependencyResolutionService (Stage 15).

All tests are fully deterministic — no LLM calls, no network, no external database.

Coverage
--------
Core resolution:
  RS01. Resolved mention with matching source_ref → ExplicitDependency created.
  RS02. Unresolved mention (entity_id=None) → no dependency.
  RS03. Missing mention_id → no dependency (graceful skip).
  RS04. Source ref validation: source_ref does NOT match mention entity → discard.
  RS05. Target does not resolve to any entity → discard.
  RS06. Self-relationship (source == target entity_id) → discarded.
  RS07. BLOCKS relationship resolved correctly with correct direction.
  RS08. Passive BLOCKS ("Y is blocked by X") → X BLOCKS Y, direction verified.

Idempotency and deduplication:
  RS09. Re-processing same mention → same dependency_id (idempotent).
  RS10. Same source+target+type, two different meetings → two distinct records.
  RS11. resolve_meeting_dependencies processes all resolved mentions in a meeting.

Canonical name matching:
  RS12. Source ref matches alias (not just canonical name) → resolved.
  RS13. Source ref case-insensitive match → resolved.

Entity service integration:
  RS14. EntityService without dependency_resolution_service → works as before.
  RS15. EntityService with dependency_resolution_service → dependency auto-extracted on resolve.
  RS16. EntityService: unresolved mention → no dependency extraction triggered.
  RS17. EntityService: dependency extraction failure → mention still returned.
"""

from datetime import datetime, timezone

import pytest

from app.models.entity import CanonicalEntity, EntityMention, EntityType, ResolutionStatus
from app.models.relationships import RelationshipType
from app.repositories.dependency_repository import InMemoryDependencyRepository
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.dependency_resolution_service import DependencyResolutionService
from app.services.entity_service import EntityService


# ---------------------------------------------------------------------------
# Shared builder helpers
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


def _make_entity(entity_id: str, canonical_name: str, entity_type: EntityType = EntityType.ISSUE, aliases=None) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        canonical_name=canonical_name,
        aliases=aliases or [],
        created_at=_BASE_TIME,
    )


def _make_mention(
    mention_id: str,
    entity_id: str | None,
    meeting_id: str,
    source_text: str,
    entity_type: EntityType = EntityType.ISSUE,
    status: ResolutionStatus = ResolutionStatus.RESOLVED,
) -> EntityMention:
    return EntityMention(
        mention_id=mention_id,
        entity_type=entity_type,
        text="text",
        meeting_id=meeting_id,
        source_text=source_text,
        entity_id=entity_id,
        resolution_status=status,
        created_at=_BASE_TIME,
    )


def _build_service(
    entities=None,
    mentions=None,
) -> tuple[DependencyResolutionService, InMemoryEntityRepository, InMemoryMentionRepository, InMemoryDependencyRepository]:
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    dependency_repo = InMemoryDependencyRepository()

    for e in (entities or []):
        entity_repo.create(e)
    for m in (mentions or []):
        mention_repo.create(m)

    service = DependencyResolutionService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        dependency_repo=dependency_repo,
    )
    return service, entity_repo, mention_repo, dependency_repo


# ===========================================================================
# RS01: Resolved mention with matching source_ref → dependency created
# ===========================================================================

def test_rs01_resolved_mention_creates_dependency():
    """RS01. Resolved mention with source_ref matching entity name → ExplicitDependency saved."""
    e1 = _make_entity("e1", "Auth Service")
    e2 = _make_entity("e2", "Database Migration")
    mention = _make_mention(
        "mn1",
        entity_id="e1",
        meeting_id="m1",
        source_text="Auth Service depends on Database Migration.",
    )

    service, _, _, dep_repo = _build_service(entities=[e1, e2], mentions=[mention])
    resolved = service.resolve_mention_dependencies("mn1")

    assert len(resolved) == 1
    dep = resolved[0]
    assert dep.source_entity_id == "e1"
    assert dep.target_entity_id == "e2"
    assert dep.relationship_type == RelationshipType.DEPENDS_ON
    assert dep.meeting_id == "m1"
    assert dep.mention_id == "mn1"
    assert dep.source_text == "Auth Service depends on Database Migration."

    # Also verify it was persisted
    stored = dep_repo.list_by_entity_id("e1")
    assert len(stored) == 1


# ===========================================================================
# RS02: Unresolved mention → no dependency
# ===========================================================================

def test_rs02_unresolved_mention_skipped():
    """RS02. Mention with entity_id=None → no dependency extraction."""
    mention = _make_mention(
        "mn1",
        entity_id=None,
        meeting_id="m1",
        source_text="Auth Service depends on Database Migration.",
        status=ResolutionStatus.UNRESOLVED,
    )

    service, _, _, dep_repo = _build_service(mentions=[mention])
    resolved = service.resolve_mention_dependencies("mn1")

    assert resolved == []
    assert dep_repo.list_all() == []


# ===========================================================================
# RS03: Missing mention_id → no dependency
# ===========================================================================

def test_rs03_missing_mention_id():
    """RS03. mention_id does not exist → empty result, no error."""
    service, _, _, dep_repo = _build_service()
    resolved = service.resolve_mention_dependencies("nonexistent-mention")
    assert resolved == []


# ===========================================================================
# RS04: Source ref validation — source_ref does NOT match mention entity
# ===========================================================================

def test_rs04_source_ref_mismatch_discarded():
    """RS04. Source ref in extracted statement does NOT match the mention's entity.

    If mention is resolved to 'Database Migration' but source_text says
    'Auth Service depends on Deployment', the extracted source_ref 'Auth Service'
    does not match 'Database Migration' → relationship discarded.
    """
    e1 = _make_entity("e1", "Database Migration")
    e2 = _make_entity("e2", "Deployment")
    # Mention resolved to e1 (Database Migration), but source_text is about Auth Service
    mention = _make_mention(
        "mn1",
        entity_id="e1",
        meeting_id="m1",
        source_text="Auth Service depends on Deployment.",
    )

    service, _, _, dep_repo = _build_service(entities=[e1, e2], mentions=[mention])
    resolved = service.resolve_mention_dependencies("mn1")

    # Source ref 'Auth Service' does not match entity 'Database Migration' → discarded
    assert resolved == []
    assert dep_repo.list_all() == []


# ===========================================================================
# RS05: Target does not resolve → discard
# ===========================================================================

def test_rs05_unresolvable_target_discarded():
    """RS05. Target ref does not match any canonical entity → relationship discarded."""
    e1 = _make_entity("e1", "Auth Service")
    # 'Database Migration' entity does NOT exist in the repo
    mention = _make_mention(
        "mn1",
        entity_id="e1",
        meeting_id="m1",
        source_text="Auth Service depends on Database Migration.",
    )

    service, _, _, dep_repo = _build_service(entities=[e1], mentions=[mention])
    resolved = service.resolve_mention_dependencies("mn1")

    assert resolved == []
    assert dep_repo.list_all() == []


# ===========================================================================
# RS06: Self-relationship discarded
# ===========================================================================

def test_rs06_self_relationship_discarded():
    """RS06. If source and target resolve to the same entity → discarded."""
    e1 = _make_entity("e1", "Auth Service", aliases=["Auth"])
    # Source text: "Auth Service depends on Auth" — both resolve to e1
    mention = _make_mention(
        "mn1",
        entity_id="e1",
        meeting_id="m1",
        source_text="Auth Service depends on Auth.",
    )

    service, _, _, dep_repo = _build_service(entities=[e1], mentions=[mention])
    resolved = service.resolve_mention_dependencies("mn1")

    # "Auth" alias resolves to same entity as "Auth Service" → self-relationship → discarded
    assert resolved == []


# ===========================================================================
# RS07: BLOCKS relationship resolved correctly
# ===========================================================================

def test_rs07_blocks_relationship_resolved():
    """RS07. 'X is blocking Y' → BLOCKS relationship with correct direction."""
    e1 = _make_entity("e1", "Database Migration")
    e2 = _make_entity("e2", "Deployment")
    mention = _make_mention(
        "mn1",
        entity_id="e1",
        meeting_id="m1",
        source_text="Database Migration is blocking Deployment.",
    )

    service, _, _, dep_repo = _build_service(entities=[e1, e2], mentions=[mention])
    resolved = service.resolve_mention_dependencies("mn1")

    assert len(resolved) == 1
    dep = resolved[0]
    assert dep.relationship_type == RelationshipType.BLOCKS
    assert dep.source_entity_id == "e1"  # Database Migration → blocker
    assert dep.target_entity_id == "e2"  # Deployment → blocked


# ===========================================================================
# RS08: Passive BLOCKS direction verified
# ===========================================================================

def test_rs08_passive_blocks_direction_correct():
    """RS08. 'Y is blocked by X' — mention is about the BLOCKER (X).

    The mention's source_text says "Payment API is blocked by Database Migration."
    The mention is resolved to entity Database Migration (the blocker).
    The extractor produces: source_ref="Database Migration", target_ref="Payment API".
    source_ref matches the mention's entity → BLOCKS created: Database Migration BLOCKS Payment API.
    """
    e1 = _make_entity("e1", "Database Migration")  # The blocker
    e2 = _make_entity("e2", "Payment API")          # Being blocked
    mention = _make_mention(
        "mn1",
        entity_id="e1",  # mention is about Database Migration
        meeting_id="m1",
        source_text="Payment API is blocked by Database Migration.",
    )

    service, _, _, dep_repo = _build_service(entities=[e1, e2], mentions=[mention])
    resolved = service.resolve_mention_dependencies("mn1")

    assert len(resolved) == 1
    dep = resolved[0]
    assert dep.relationship_type == RelationshipType.BLOCKS
    assert dep.source_entity_id == "e1"  # Database Migration is the blocker
    assert dep.target_entity_id == "e2"  # Payment API is being blocked


# ===========================================================================
# RS09: Idempotency — same mention re-processed → same dependency_id
# ===========================================================================

def test_rs09_idempotency():
    """RS09. Re-processing the same mention produces the same dependency_id."""
    e1 = _make_entity("e1", "Auth Service")
    e2 = _make_entity("e2", "Database Migration")
    mention = _make_mention(
        "mn1",
        entity_id="e1",
        meeting_id="m1",
        source_text="Auth Service depends on Database Migration.",
    )

    service, _, _, dep_repo = _build_service(entities=[e1, e2], mentions=[mention])

    result1 = service.resolve_mention_dependencies("mn1")
    result2 = service.resolve_mention_dependencies("mn1")

    assert len(result1) == 1
    assert len(result2) == 1
    # Same dependency_id
    assert result1[0].dependency_id == result2[0].dependency_id
    # Only one record in the repo (upsert)
    assert len(dep_repo.list_all()) == 1


# ===========================================================================
# RS10: Same source+target, two meetings → two records
# ===========================================================================

def test_rs10_same_relationship_two_meetings_two_records():
    """RS10. Same logical relationship observed in two meetings → two distinct records."""
    e1 = _make_entity("e1", "Auth Service")
    e2 = _make_entity("e2", "Database Migration")

    mention1 = _make_mention(
        "mn1",
        entity_id="e1",
        meeting_id="m1",
        source_text="Auth Service depends on Database Migration.",
    )
    mention2 = _make_mention(
        "mn2",
        entity_id="e1",
        meeting_id="m2",  # Different meeting
        source_text="Auth Service depends on Database Migration.",
    )

    service, _, _, dep_repo = _build_service(
        entities=[e1, e2], mentions=[mention1, mention2]
    )

    r1 = service.resolve_mention_dependencies("mn1")
    r2 = service.resolve_mention_dependencies("mn2")

    assert len(r1) == 1
    assert len(r2) == 1
    # Different meeting → different dependency_id
    assert r1[0].dependency_id != r2[0].dependency_id
    # Two distinct records in repo
    assert len(dep_repo.list_all()) == 2


# ===========================================================================
# RS11: resolve_meeting_dependencies — processes all resolved mentions
# ===========================================================================

def test_rs11_resolve_meeting_processes_all_mentions():
    """RS11. resolve_meeting_dependencies processes every resolved mention in the meeting."""
    e1 = _make_entity("e1", "Auth Service")
    e2 = _make_entity("e2", "Database Migration")
    e3 = _make_entity("e3", "Deployment")

    mentions = [
        _make_mention("mn1", "e1", "m1", "Auth Service depends on Database Migration."),
        _make_mention("mn2", "e2", "m1", "Database Migration is blocking Deployment."),
        _make_mention("mn3", None, "m1", "some unresolved text", status=ResolutionStatus.UNRESOLVED),
    ]

    service, _, _, dep_repo = _build_service(entities=[e1, e2, e3], mentions=mentions)
    resolved = service.resolve_meeting_dependencies("m1")

    assert len(resolved) == 2
    types = {(d.source_entity_id, d.target_entity_id, d.relationship_type) for d in resolved}
    assert ("e1", "e2", RelationshipType.DEPENDS_ON) in types
    assert ("e2", "e3", RelationshipType.BLOCKS) in types


# ===========================================================================
# RS12: Source ref matches alias
# ===========================================================================

def test_rs12_source_ref_matches_alias():
    """RS12. Source ref matches an alias (not just canonical name) → resolved."""
    e1 = _make_entity("e1", "Authentication Service", aliases=["Auth"])
    e2 = _make_entity("e2", "Database Migration")
    mention = _make_mention(
        "mn1",
        entity_id="e1",
        meeting_id="m1",
        source_text="Auth depends on Database Migration.",
    )

    service, _, _, dep_repo = _build_service(entities=[e1, e2], mentions=[mention])
    resolved = service.resolve_mention_dependencies("mn1")

    # "Auth" matches alias of e1 → source ref validated → dependency created
    assert len(resolved) == 1
    assert resolved[0].source_entity_id == "e1"


# ===========================================================================
# RS13: Source ref case-insensitive match
# ===========================================================================

def test_rs13_source_ref_case_insensitive():
    """RS13. Source ref case-insensitive match → source validated correctly."""
    e1 = _make_entity("e1", "Auth Service")
    e2 = _make_entity("e2", "Database Migration")
    mention = _make_mention(
        "mn1",
        entity_id="e1",
        meeting_id="m1",
        source_text="AUTH SERVICE depends on Database Migration.",
    )

    service, _, _, dep_repo = _build_service(entities=[e1, e2], mentions=[mention])
    resolved = service.resolve_mention_dependencies("mn1")

    assert len(resolved) == 1
    assert resolved[0].source_entity_id == "e1"


# ===========================================================================
# RS14-RS17: EntityService integration
# ===========================================================================

def _build_entity_service(
    entities=None,
    with_dep_service=False,
) -> tuple[EntityService, InMemoryEntityRepository, InMemoryMentionRepository, InMemoryDependencyRepository]:
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    dependency_repo = InMemoryDependencyRepository()

    for e in (entities or []):
        entity_repo.create(e)

    dep_resolution_service = None
    if with_dep_service:
        dep_resolution_service = DependencyResolutionService(
            entity_repo=entity_repo,
            mention_repo=mention_repo,
            dependency_repo=dependency_repo,
        )

    service = EntityService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        dependency_resolution_service=dep_resolution_service,
    )
    return service, entity_repo, mention_repo, dependency_repo


def test_rs14_entity_service_without_dep_service_works():
    """RS14. EntityService without dependency_resolution_service works normally."""
    e1 = _make_entity("e1", "Auth Service")
    entity_service, entity_repo, mention_repo, dep_repo = _build_entity_service(
        entities=[e1], with_dep_service=False
    )

    mention = entity_service.register_mention(
        entity_type=EntityType.ISSUE,
        text="Auth Service",
        meeting_id="m1",
        source_text="Auth Service depends on Database Migration.",
    )
    assert mention.entity_id == "e1"
    assert mention.resolution_status == ResolutionStatus.RESOLVED
    # No dependency extraction was wired
    assert dep_repo.list_all() == []


def test_rs15_entity_service_with_dep_service_auto_extracts():
    """RS15. EntityService with DependencyResolutionService automatically extracts on resolve."""
    e1 = _make_entity("e1", "Auth Service")
    e2 = _make_entity("e2", "Database Migration")

    entity_service, entity_repo, mention_repo, dep_repo = _build_entity_service(
        entities=[e1, e2], with_dep_service=True
    )

    mention = entity_service.register_mention(
        entity_type=EntityType.ISSUE,
        text="Auth Service",
        meeting_id="m1",
        source_text="Auth Service depends on Database Migration.",
    )

    assert mention.resolution_status == ResolutionStatus.RESOLVED
    deps = dep_repo.list_all()
    assert len(deps) == 1
    dep = deps[0]
    assert dep.source_entity_id == "e1"
    assert dep.target_entity_id == "e2"
    assert dep.relationship_type == RelationshipType.DEPENDS_ON


def test_rs16_entity_service_unresolved_no_extraction():
    """RS16. When mention is UNRESOLVED, no dependency extraction runs."""
    # No entity 'Unknown Component' in repo → mention will be UNRESOLVED
    entity_service, _, _, dep_repo = _build_entity_service(with_dep_service=True)

    mention = entity_service.register_mention(
        entity_type=EntityType.ISSUE,
        text="Unknown Component",
        meeting_id="m1",
        source_text="Unknown Component depends on Something.",
    )

    assert mention.resolution_status == ResolutionStatus.UNRESOLVED
    assert dep_repo.list_all() == []


def test_rs17_entity_service_dep_extraction_failure_does_not_block():
    """RS17. If dependency extraction raises an exception, mention is still returned."""
    # We simulate failure by providing a broken dep_resolution_service
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()

    e1 = _make_entity("e1", "Auth Service")
    entity_repo.create(e1)

    class BrokenDepService:
        def resolve_mention_dependencies(self, mention_id):
            raise RuntimeError("Simulated extraction failure")

    entity_service = EntityService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        dependency_resolution_service=BrokenDepService(),
    )

    # Should NOT raise — the failure is swallowed and logged
    mention = entity_service.register_mention(
        entity_type=EntityType.ISSUE,
        text="Auth Service",
        meeting_id="m1",
        source_text="Auth Service depends on Database Migration.",
    )

    assert mention.resolution_status == ResolutionStatus.RESOLVED
    assert mention.entity_id == "e1"
