"""Stage 15 extended tests for the Entity Relationship Service.

Supplements test_relationships.py with directional correctness, deduplication,
and semantic constraint verification.

Coverage
--------
  R15. DEPENDS_ON is directional: source depends on target, not vice versa.
  R16. BLOCKS is directional: source blocks target, never reversed.
  R17. Same DEPENDS_ON across multiple meetings → strength = meeting count.
  R18. CO_OCCURS_WITH is NEVER promoted to DEPENDS_ON or BLOCKS.
  R19. Explicit dependency relationship has correct evidence fields.
  R20. DEPENDS_ON and BLOCKS are distinct types for the same entity pair.
  R21. Graph endpoint shows both CO_OCCURS_WITH and explicit dependencies.
  R22. Dependency endpoint excludes CO_OCCURS_WITH relationships.
"""

from datetime import datetime, timezone
from typing import Optional

import pytest

from app.models.dependency import ExplicitDependency
from app.models.entity import (
    CanonicalEntity,
    EntityMention,
    EntityType,
    ResolutionStatus,
)
from app.models.relationships import EntityRelationshipGraph, RelationshipType
from app.repositories.dependency_repository import InMemoryDependencyRepository
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.entity_relationship_service import EntityRelationshipService

_BASE_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


def _make_entity(entity_id: str, canonical_name: str) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=EntityType.ISSUE,
        canonical_name=canonical_name,
        aliases=[],
        created_at=_BASE_TIME,
    )


def _make_mention(
    mention_id: str,
    entity_id: Optional[str],
    meeting_id: str,
    status: ResolutionStatus,
) -> EntityMention:
    return EntityMention(
        mention_id=mention_id,
        entity_type=EntityType.ISSUE,
        text="text",
        meeting_id=meeting_id,
        source_text="source text",
        entity_id=entity_id,
        resolution_status=status,
        created_at=_BASE_TIME,
    )


def _build_service(entities=None, mentions=None, dependencies=None) -> EntityRelationshipService:
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    dependency_repo = InMemoryDependencyRepository()

    for e in (entities or []):
        entity_repo.create(e)
    for m in (mentions or []):
        mention_repo.create(m)
    for d in (dependencies or []):
        dependency_repo.save(d)

    return EntityRelationshipService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
        dependency_repo=dependency_repo,
    )


def _make_dep(
    dep_id: str,
    mention_id: str,
    source_id: str,
    target_id: str,
    rel_type: RelationshipType,
    meeting_id: str,
    source_text: str = "evidence text",
) -> ExplicitDependency:
    return ExplicitDependency(
        dependency_id=dep_id,
        mention_id=mention_id,
        source_entity_id=source_id,
        target_entity_id=target_id,
        relationship_type=rel_type,
        source_text=source_text,
        meeting_id=meeting_id,
    )


# ---------------------------------------------------------------------------
# R15: DEPENDS_ON is directional
# ---------------------------------------------------------------------------

def test_r15_depends_on_is_directional() -> None:
    """R15. DEPENDS_ON is directional: source depends on target, not vice versa."""
    e1 = _make_entity("e1", "auth service")
    e2 = _make_entity("e2", "database migration")

    dep = _make_dep(
        "dep1", "mn1", "e1", "e2",
        RelationshipType.DEPENDS_ON, "m-1",
        "Auth Service depends on Database Migration.",
    )

    service = _build_service(entities=[e1, e2], dependencies=[dep])

    result_e1 = service.get_dependency_relationships("e1")
    assert any(
        r.relationship_type == RelationshipType.DEPENDS_ON
        and r.source_entity_id == "e1"
        and r.target_entity_id == "e2"
        for r in result_e1
    ), "e1's perspective should see DEPENDS_ON e1→e2"

    result_e2 = service.get_dependency_relationships("e2")
    # Edge is visible from e2's perspective but direction stays e1→e2
    assert any(
        r.relationship_type == RelationshipType.DEPENDS_ON
        and r.source_entity_id == "e1"
        and r.target_entity_id == "e2"
        for r in result_e2
    ), "e2's perspective should also see the e1→e2 edge"

    # The direction must NOT be reversed (e2 DEPENDS_ON e1 must not appear)
    assert not any(
        r.relationship_type == RelationshipType.DEPENDS_ON
        and r.source_entity_id == "e2"
        and r.target_entity_id == "e1"
        for r in result_e2
    ), "Reversed DEPENDS_ON e2→e1 must not appear"


# ---------------------------------------------------------------------------
# R16: BLOCKS is directional
# ---------------------------------------------------------------------------

def test_r16_blocks_is_directional() -> None:
    """R16. BLOCKS is directional: source=blocker, target=blocked."""
    e1 = _make_entity("e1", "database migration")  # blocker
    e2 = _make_entity("e2", "deployment")           # blocked

    dep = _make_dep(
        "dep1", "mn1", "e1", "e2",
        RelationshipType.BLOCKS, "m-1",
        "Database Migration is blocking Deployment.",
    )

    service = _build_service(entities=[e1, e2], dependencies=[dep])

    result_e1 = service.get_dependency_relationships("e1")
    blocks = [r for r in result_e1 if r.relationship_type == RelationshipType.BLOCKS]
    assert len(blocks) == 1
    assert blocks[0].source_entity_id == "e1"  # Database Migration is blocker
    assert blocks[0].target_entity_id == "e2"  # Deployment is blocked

    # Must NOT see e2 BLOCKS e1 from any perspective
    result_e2 = service.get_dependency_relationships("e2")
    assert not any(
        r.relationship_type == RelationshipType.BLOCKS
        and r.source_entity_id == "e2"
        and r.target_entity_id == "e1"
        for r in result_e2
    ), "Reversed BLOCKS e2→e1 must not appear"


# ---------------------------------------------------------------------------
# R17: Multi-meeting strength aggregation
# ---------------------------------------------------------------------------

def test_r17_depends_on_multi_meeting_strength() -> None:
    """R17. Same DEPENDS_ON across N meetings → strength=N, all meetings returned."""
    e1 = _make_entity("e1", "auth service")
    e2 = _make_entity("e2", "database migration")

    deps = [
        _make_dep(f"dep{i}", f"mn{i}", "e1", "e2", RelationshipType.DEPENDS_ON, f"m-{i}")
        for i in range(1, 4)
    ]

    service = _build_service(entities=[e1, e2], dependencies=deps)
    result = service.get_dependency_relationships("e1")

    dep_rels = [r for r in result if r.relationship_type == RelationshipType.DEPENDS_ON]
    assert len(dep_rels) == 1, "Same logical relationship → one aggregated edge"
    assert dep_rels[0].strength == 3
    assert set(dep_rels[0].related_meeting_ids) == {"m-1", "m-2", "m-3"}


# ---------------------------------------------------------------------------
# R18: CO_OCCURS_WITH not promoted to dependency
# ---------------------------------------------------------------------------

def test_r18_co_occurs_with_never_produces_dependency() -> None:
    """R18. CO_OCCURS_WITH is NEVER promoted to DEPENDS_ON or BLOCKS.

    Strong co-occurrence (3 meetings) must NOT produce any explicit dependency.
    """
    e1 = _make_entity("e1", "auth service")
    e2 = _make_entity("e2", "database migration")

    mentions = []
    for i in range(1, 4):
        mentions.append(_make_mention(f"mn{2*i-1}", "e1", f"m-{i}", ResolutionStatus.RESOLVED))
        mentions.append(_make_mention(f"mn{2*i}", "e2", f"m-{i}", ResolutionStatus.RESOLVED))

    service = _build_service(entities=[e1, e2], mentions=mentions)

    # Dependency endpoint must return nothing
    deps = service.get_dependency_relationships("e1")
    assert len(deps) == 0, "CO_OCCURS_WITH must not be promoted to dependency"

    # Relationship graph must only contain CO_OCCURS_WITH
    graph = service.get_relationship_graph("e1")
    dep_or_blocks = [
        r for r in graph.relationships
        if r.relationship_type in (RelationshipType.DEPENDS_ON, RelationshipType.BLOCKS)
    ]
    assert len(dep_or_blocks) == 0


# ---------------------------------------------------------------------------
# R19: Evidence fields are correct
# ---------------------------------------------------------------------------

def test_r19_dependency_evidence_fields() -> None:
    """R19. EntityRelationship from dependency has correct evidence fields."""
    e1 = _make_entity("e1", "auth service")
    e2 = _make_entity("e2", "database migration")

    dep = _make_dep(
        "dep1", "mn1", "e1", "e2",
        RelationshipType.DEPENDS_ON, "m-1",
        "Auth Service depends on Database Migration.",
    )

    service = _build_service(entities=[e1, e2], dependencies=[dep])
    result = service.get_dependency_relationships("e1")

    assert len(result) == 1
    rel = result[0]
    assert rel.evidence_type.value == "EXPLICIT_STATEMENT"
    assert rel.source_text == "Auth Service depends on Database Migration."
    assert rel.mention_id == "mn1"
    assert rel.related_meeting_ids == ["m-1"]


# ---------------------------------------------------------------------------
# R20: DEPENDS_ON and BLOCKS are distinct
# ---------------------------------------------------------------------------

def test_r20_depends_on_and_blocks_distinct_types() -> None:
    """R20. DEPENDS_ON and BLOCKS are separate logical edges."""
    e1 = _make_entity("e1", "auth service")
    e2 = _make_entity("e2", "database migration")

    dep1 = _make_dep("dep1", "mn1", "e1", "e2", RelationshipType.DEPENDS_ON, "m-1")
    dep2 = _make_dep("dep2", "mn2", "e2", "e1", RelationshipType.BLOCKS, "m-2")

    service = _build_service(entities=[e1, e2], dependencies=[dep1, dep2])
    result_e1 = service.get_dependency_relationships("e1")

    types = {r.relationship_type for r in result_e1}
    assert RelationshipType.DEPENDS_ON in types
    assert RelationshipType.BLOCKS in types
    assert len(result_e1) == 2


# ---------------------------------------------------------------------------
# R21: Graph endpoint shows both CO_OCCURS_WITH and explicit dependencies
# ---------------------------------------------------------------------------

def test_r21_graph_includes_both_co_occurrence_and_explicit() -> None:
    """R21. get_relationship_graph returns CO_OCCURS_WITH AND explicit dependency types."""
    e1 = _make_entity("e1", "auth service")
    e2 = _make_entity("e2", "database migration")
    e3 = _make_entity("e3", "deployment")

    # e1 and e3 co-occur
    mentions = [
        _make_mention("mn1", "e1", "m-1", ResolutionStatus.RESOLVED),
        _make_mention("mn2", "e3", "m-1", ResolutionStatus.RESOLVED),
    ]

    # e1 DEPENDS_ON e2 (explicit)
    dep = _make_dep("dep1", "mn1", "e1", "e2", RelationshipType.DEPENDS_ON, "m-2")

    service = _build_service(entities=[e1, e2, e3], mentions=mentions, dependencies=[dep])
    graph = service.get_relationship_graph("e1")

    rel_types = {r.relationship_type for r in graph.relationships}
    assert RelationshipType.CO_OCCURS_WITH in rel_types
    assert RelationshipType.DEPENDS_ON in rel_types
    assert graph.relationship_count == 2


# ---------------------------------------------------------------------------
# R22: Dependency endpoint excludes CO_OCCURS_WITH
# ---------------------------------------------------------------------------

def test_r22_dependency_endpoint_excludes_co_occurs_with() -> None:
    """R22. get_dependency_relationships returns only DEPENDS_ON/BLOCKS, not CO_OCCURS_WITH."""
    e1 = _make_entity("e1", "auth service")
    e2 = _make_entity("e2", "database migration")
    e3 = _make_entity("e3", "deployment")

    # e1 and e3 co-occur
    mentions = [
        _make_mention("mn1", "e1", "m-1", ResolutionStatus.RESOLVED),
        _make_mention("mn2", "e3", "m-1", ResolutionStatus.RESOLVED),
    ]

    # e1 DEPENDS_ON e2 (explicit)
    dep = _make_dep("dep1", "mn1", "e1", "e2", RelationshipType.DEPENDS_ON, "m-2")

    service = _build_service(entities=[e1, e2, e3], mentions=mentions, dependencies=[dep])
    deps = service.get_dependency_relationships("e1")

    # Must only include explicit DEPENDS_ON
    assert all(r.relationship_type != RelationshipType.CO_OCCURS_WITH for r in deps)
    assert len(deps) == 1
    assert deps[0].relationship_type == RelationshipType.DEPENDS_ON
