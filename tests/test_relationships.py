"""Tests for the Entity Relationship & Dependency Intelligence Engine (Stage 12).

All tests are fully deterministic -- no LLM calls, no network, no external database.

Coverage
--------

Service unit tests (no HTTP):
  R01. Unknown entity_id raises EntityNotFoundError.
  R02. Entity with no related entities returns empty relationships list.
  R03. Single CO_OCCURS_WITH relationship correctly inferred from a shared meeting.
  R04. Strength (weight) correctly counts the number of distinct meetings two entities co-occur in.
  R05. Deduplication: multiple mentions in the same meeting only count as 1 co-occurrence.
  R06. Symmetry: Relationships are always undirected (source is the alphabetically smaller entity_id).
  R07. Unresolved/Ambiguous mentions are ignored.
  R08. Deterministic sort key generation matches the format: {strength:06d}_{type}_{target}_{id}.
  R09. Sorting order: Output is strictly ordered by Strength DESC, Deterministic Sort Key ASC.
  R10. Self-loops are ignored (an entity co-occurring with itself does not produce a relationship).

API endpoint tests (full stack via TestClient):
  R11. GET /{entity_id}/relationships returns correct JSON structure.
  R12. API response validates against EntityRelationshipGraphResponse schema.

Regression:
  R13. All existing tests pass (verified separately).
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
from app.models.relationships import EntityRelationshipGraph
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.entity_relationship_service import EntityRelationshipService
from app.services.entity_service import EntityNotFoundError

# ---------------------------------------------------------------------------
# Shared builder helpers
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)

def _make_entity(entity_id: str, canonical_name: str) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=EntityType.PERSON,
        canonical_name=canonical_name,
        aliases=[],
        created_at=_BASE_TIME,
    )

def _make_mention(mention_id: str, entity_id: Optional[str], meeting_id: str, status: ResolutionStatus) -> EntityMention:
    return EntityMention(
        mention_id=mention_id,
        entity_type=EntityType.PERSON,
        text="text",
        meeting_id=meeting_id,
        source_text="source text",
        entity_id=entity_id,
        resolution_status=status,
        created_at=_BASE_TIME,
    )

def _build_service(entities=None, mentions=None) -> EntityRelationshipService:
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()

    for e in (entities or []):
        entity_repo.create(e)
    for m in (mentions or []):
        mention_repo.create(m)

    return EntityRelationshipService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
    )

# ---------------------------------------------------------------------------
# R01: Unknown entity_id raises EntityNotFoundError
# ---------------------------------------------------------------------------
def test_r01_unknown_entity_raises_not_found() -> None:
    service = _build_service()
    with pytest.raises(EntityNotFoundError):
        service.get_relationship_graph("nonexistent-entity-id")

# ---------------------------------------------------------------------------
# R02: Entity with no related entities returns empty relationships list
# ---------------------------------------------------------------------------
def test_r02_no_relationships() -> None:
    entity = _make_entity("e1", "rahul kumar")
    service = _build_service(entities=[entity])
    result = service.get_relationship_graph("e1")

    assert isinstance(result, EntityRelationshipGraph)
    assert result.entity_id == "e1"
    assert result.relationship_count == 0
    assert result.relationships == []
    assert result.related_entity_ids == []

# ---------------------------------------------------------------------------
# R03: Single CO_OCCURS_WITH relationship correctly inferred from a shared meeting
# ---------------------------------------------------------------------------
def test_r03_single_co_occurrence() -> None:
    e1 = _make_entity("e1", "rahul kumar")
    e2 = _make_entity("e2", "priya sharma")
    
    m1 = _make_mention("mn1", "e1", "meeting-1", ResolutionStatus.RESOLVED)
    m2 = _make_mention("mn2", "e2", "meeting-1", ResolutionStatus.RESOLVED)
    
    service = _build_service(entities=[e1, e2], mentions=[m1, m2])
    result = service.get_relationship_graph("e1")
    
    assert result.relationship_count == 1
    rel = result.relationships[0]
    assert rel.relationship_type.value == "CO_OCCURS_WITH"
    assert rel.strength == 1
    assert rel.related_meeting_ids == ["meeting-1"]
    assert rel.target_entity_id == "e2"
    assert "e2" in result.related_entity_ids

# ---------------------------------------------------------------------------
# R04: Strength (weight) correctly counts distinct meetings
# ---------------------------------------------------------------------------
def test_r04_strength_counts_distinct_meetings() -> None:
    e1 = _make_entity("e1", "rahul kumar")
    e2 = _make_entity("e2", "priya sharma")
    
    mentions = [
        _make_mention("mn1", "e1", "meeting-1", ResolutionStatus.RESOLVED),
        _make_mention("mn2", "e2", "meeting-1", ResolutionStatus.RESOLVED),
        _make_mention("mn3", "e1", "meeting-2", ResolutionStatus.RESOLVED),
        _make_mention("mn4", "e2", "meeting-2", ResolutionStatus.RESOLVED),
        _make_mention("mn5", "e1", "meeting-3", ResolutionStatus.RESOLVED),
        _make_mention("mn6", "e2", "meeting-3", ResolutionStatus.RESOLVED),
    ]
    
    service = _build_service(entities=[e1, e2], mentions=mentions)
    result = service.get_relationship_graph("e1")
    
    assert result.relationship_count == 1
    assert result.relationships[0].strength == 3
    assert set(result.relationships[0].related_meeting_ids) == {"meeting-1", "meeting-2", "meeting-3"}

# ---------------------------------------------------------------------------
# R05: Deduplication: multiple mentions in the same meeting = 1 co-occurrence
# ---------------------------------------------------------------------------
def test_r05_multiple_mentions_same_meeting_deduplicated() -> None:
    e1 = _make_entity("e1", "rahul kumar")
    e2 = _make_entity("e2", "priya sharma")
    
    mentions = [
        _make_mention("mn1", "e1", "meeting-1", ResolutionStatus.RESOLVED),
        _make_mention("mn2", "e1", "meeting-1", ResolutionStatus.RESOLVED),
        _make_mention("mn3", "e1", "meeting-1", ResolutionStatus.RESOLVED),
        _make_mention("mn4", "e2", "meeting-1", ResolutionStatus.RESOLVED),
        _make_mention("mn5", "e2", "meeting-1", ResolutionStatus.RESOLVED),
    ]
    
    service = _build_service(entities=[e1, e2], mentions=mentions)
    result = service.get_relationship_graph("e1")
    
    assert result.relationship_count == 1
    assert result.relationships[0].strength == 1

# ---------------------------------------------------------------------------
# R06: Symmetry: source is the alphabetically smaller entity_id
# ---------------------------------------------------------------------------
def test_r06_symmetry() -> None:
    e1 = _make_entity("alpha", "alpha")
    e2 = _make_entity("zeta", "zeta")
    
    m1 = _make_mention("mn1", "alpha", "meeting-1", ResolutionStatus.RESOLVED)
    m2 = _make_mention("mn2", "zeta", "meeting-1", ResolutionStatus.RESOLVED)
    
    service = _build_service(entities=[e1, e2], mentions=[m1, m2])
    
    result_alpha = service.get_relationship_graph("alpha")
    result_zeta = service.get_relationship_graph("zeta")
    
    assert result_alpha.relationships[0].source_entity_id == "alpha"
    assert result_alpha.relationships[0].target_entity_id == "zeta"
    
    # Querying zeta still yields the exact same canonical edge
    assert result_zeta.relationships[0].source_entity_id == "alpha"
    assert result_zeta.relationships[0].target_entity_id == "zeta"

# ---------------------------------------------------------------------------
# R07: Unresolved/Ambiguous mentions are ignored
# ---------------------------------------------------------------------------
def test_r07_unresolved_ignored() -> None:
    e1 = _make_entity("e1", "rahul kumar")
    e2 = _make_entity("e2", "priya sharma")
    
    mentions = [
        _make_mention("mn1", "e1", "meeting-1", ResolutionStatus.RESOLVED),
        _make_mention("mn2", None, "meeting-1", ResolutionStatus.UNRESOLVED),
        _make_mention("mn3", "e2", "meeting-2", ResolutionStatus.RESOLVED),
        _make_mention("mn4", None, "meeting-2", ResolutionStatus.AMBIGUOUS),
    ]
    
    service = _build_service(entities=[e1, e2], mentions=mentions)
    result = service.get_relationship_graph("e1")
    assert result.relationship_count == 0

# ---------------------------------------------------------------------------
# R08: Deterministic sort key generation matches format
# ---------------------------------------------------------------------------
def test_r08_sort_key_format() -> None:
    e1 = _make_entity("e1", "rahul kumar")
    e2 = _make_entity("e2", "priya sharma")
    
    m1 = _make_mention("mn1", "e1", "meeting-1", ResolutionStatus.RESOLVED)
    m2 = _make_mention("mn2", "e2", "meeting-1", ResolutionStatus.RESOLVED)
    
    service = _build_service(entities=[e1, e2], mentions=[m1, m2])
    result = service.get_relationship_graph("e1")
    
    key = result.relationships[0].deterministic_sort_key
    # Format: 000001_CO_OCCURS_WITH_e2_{uuid}
    assert key.startswith("000001_CO_OCCURS_WITH_e2_")

# ---------------------------------------------------------------------------
# R09: Sorting order
# ---------------------------------------------------------------------------
def test_r09_sorting_order() -> None:
    e1 = _make_entity("e1", "rahul kumar")
    e2 = _make_entity("e2", "priya sharma")
    e3 = _make_entity("e3", "amit singh")
    e4 = _make_entity("e4", "neha gupta")
    
    mentions = [
        # e2: strength 1
        _make_mention("mn1", "e1", "m-1", ResolutionStatus.RESOLVED),
        _make_mention("mn2", "e2", "m-1", ResolutionStatus.RESOLVED),
        # e3: strength 3
        _make_mention("mn3", "e1", "m-2", ResolutionStatus.RESOLVED),
        _make_mention("mn4", "e3", "m-2", ResolutionStatus.RESOLVED),
        _make_mention("mn5", "e1", "m-3", ResolutionStatus.RESOLVED),
        _make_mention("mn6", "e3", "m-3", ResolutionStatus.RESOLVED),
        _make_mention("mn7", "e1", "m-4", ResolutionStatus.RESOLVED),
        _make_mention("mn8", "e3", "m-4", ResolutionStatus.RESOLVED),
        # e4: strength 2
        _make_mention("mn9", "e1", "m-5", ResolutionStatus.RESOLVED),
        _make_mention("mn10", "e4", "m-5", ResolutionStatus.RESOLVED),
        _make_mention("mn11", "e1", "m-6", ResolutionStatus.RESOLVED),
        _make_mention("mn12", "e4", "m-6", ResolutionStatus.RESOLVED),
    ]
    
    service = _build_service(entities=[e1, e2, e3, e4], mentions=mentions)
    result = service.get_relationship_graph("e1")
    
    # Expected order: e3 (strength 3), e4 (strength 2), e2 (strength 1)
    # We query from "e1"'s perspective, target is the OTHER entity.
    targets = []
    for r in result.relationships:
        targets.append(r.target_entity_id if r.source_entity_id == "e1" else r.source_entity_id)
        
    assert targets == ["e3", "e4", "e2"]

# ---------------------------------------------------------------------------
# R10: Self-loops are ignored
# ---------------------------------------------------------------------------
def test_r10_self_loops_ignored() -> None:
    e1 = _make_entity("e1", "rahul kumar")
    
    mentions = [
        _make_mention("mn1", "e1", "m-1", ResolutionStatus.RESOLVED),
        _make_mention("mn2", "e1", "m-1", ResolutionStatus.RESOLVED),
    ]
    
    service = _build_service(entities=[e1], mentions=mentions)
    result = service.get_relationship_graph("e1")
    
    assert result.relationship_count == 0

# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def relationship_client():
    from app.main import app
    from app.api.entities import get_entity_relationship_service, get_entity_service

    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()

    service = EntityRelationshipService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
    )

    from app.services.entity_service import EntityService
    entity_service = EntityService(
        entity_repo=entity_repo,
        mention_repo=mention_repo,
    )

    app.dependency_overrides[get_entity_relationship_service] = lambda: service
    app.dependency_overrides[get_entity_service] = lambda: entity_service

    client = TestClient(app)
    yield client, entity_repo, mention_repo

    app.dependency_overrides.pop(get_entity_relationship_service, None)
    app.dependency_overrides.pop(get_entity_service, None)

def test_r11_api_response_has_required_fields(relationship_client) -> None:
    client, entity_repo, mention_repo = relationship_client
    
    entity_repo.create(_make_entity("e1", "rahul kumar"))
    entity_repo.create(_make_entity("e2", "priya sharma"))
    mention_repo.create(_make_mention("mn1", "e1", "m-1", ResolutionStatus.RESOLVED))
    mention_repo.create(_make_mention("mn2", "e2", "m-1", ResolutionStatus.RESOLVED))
    
    response = client.get("/api/v1/entities/e1/relationships")
    assert response.status_code == 200
    body = response.json()
    
    assert "entity_id" in body
    assert "relationship_count" in body
    assert "related_entity_ids" in body
    assert "relationships" in body
    
    assert body["relationship_count"] == 1
    rel = body["relationships"][0]
    
    assert "relationship_id" in rel
    assert "source_entity_id" in rel
    assert "target_entity_id" in rel
    assert "relationship_type" in rel
    assert "evidence_type" in rel
    assert "evidence" in rel
    assert "related_meeting_ids" in rel
    assert "strength" in rel
    assert "deterministic_sort_key" in rel

def test_r12_api_response_validates_against_schema(relationship_client) -> None:
    from app.schemas.relationships import EntityRelationshipGraphResponse
    client, entity_repo, mention_repo = relationship_client
    
    entity_repo.create(_make_entity("e1", "rahul kumar"))
    entity_repo.create(_make_entity("e2", "priya sharma"))
    mention_repo.create(_make_mention("mn1", "e1", "m-1", ResolutionStatus.RESOLVED))
    mention_repo.create(_make_mention("mn2", "e2", "m-1", ResolutionStatus.RESOLVED))
    
    response = client.get("/api/v1/entities/e1/relationships")
    body = response.json()
    
    parsed = EntityRelationshipGraphResponse(**body)
    assert parsed.entity_id == "e1"
    assert parsed.relationship_count == 1
    assert len(parsed.relationships) == 1
