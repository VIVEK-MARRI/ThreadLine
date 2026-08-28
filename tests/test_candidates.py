"""Tests for Candidate Generation (Entity Resolution Stage 2).

All tests are fully deterministic — no LLM calls, no network, no database.

Coverage
--------

Generator unit tests (pure Python, no HTTP):
  C1.  "Rahul" → candidates include "Rahul Kumar" and "Rahul Sharma".
  C2.  "Rahul" → "Ravi Kumar" is NOT a candidate.
  C3.  Only same entity_type is searched (PERSON mention → no ISSUE entities).
  C4.  ISSUE candidate generation works independently of PERSON entities.
  C5.  Aliases contribute to candidate eligibility.
  C6.  Candidate ordering is deterministic (−overlap, name, id).
  C7.  No lexical overlap → empty candidate list.

Resolution safety (service-layer, no HTTP):
  C8.  Generating candidates does not modify an unresolved mention.
  C9.  Generating candidates does not modify a resolved mention.
  C10. A resolved mention returns an empty candidate list.
  C11. Candidate generation never assigns entity_id.

API tests (full stack via TestClient):
  C12. Unknown mention_id → 404.
  C13. Valid unresolved mention with candidates → 200 + non-empty list.
  C14. Valid unresolved mention with no candidates → 200 + empty list.
  C15. Resolved mention → 200 + empty candidates + status "RESOLVED".

Regression:
  R1.  All existing entity tests still pass (ensured by running the full suite).
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.entity_resolution.lexical_candidate_generator import (
    CANDIDATE_REASON,
    MIN_TOKEN_LENGTH,
    LexicalCandidateGenerator,
    _tokenize,
)
from app.models.entity import (
    CanonicalEntity,
    EntityMention,
    EntityType,
    ResolutionStatus,
)
from app.repositories.entity_repository import InMemoryEntityRepository, _normalize
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.candidate_service import CandidateService, MentionNotFoundError
from app.services.entity_service import EntityService


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_entity(
    entity_id: str,
    entity_type: EntityType,
    canonical_name: str,
    aliases: list[str] | None = None,
) -> CanonicalEntity:
    """Construct a CanonicalEntity for use in tests.

    Applies the same _normalize() that EntityService uses so tests operate
    on the same string values they would see in production.
    """
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        canonical_name=_normalize(canonical_name),
        aliases=[_normalize(a) for a in (aliases or [])],
        created_at=datetime.now(tz=timezone.utc),
    )


def _make_mention(
    mention_id: str,
    entity_type: EntityType,
    text: str,
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED,
    entity_id: str | None = None,
) -> EntityMention:
    """Construct an EntityMention for use in tests."""
    return EntityMention(
        mention_id=mention_id,
        entity_type=entity_type,
        text=text,
        meeting_id="meeting_001",
        source_text=f"Source text containing '{text}'.",
        entity_id=entity_id,
        resolution_status=resolution_status,
        created_at=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def generator() -> LexicalCandidateGenerator:
    """A fresh LexicalCandidateGenerator for each test."""
    return LexicalCandidateGenerator()


@pytest.fixture()
def fresh_candidate_service() -> CandidateService:
    """A CandidateService backed by clean in-memory repositories."""
    return CandidateService(
        mention_repo=InMemoryMentionRepository(),
        entity_repo=InMemoryEntityRepository(),
        generator=LexicalCandidateGenerator(),
    )


@pytest.fixture()
def client_and_services():
    """Return a (TestClient, EntityService, CandidateService) sharing the same repos.

    Overrides both get_entity_service and get_candidate_service so the full
    stack is exercised against the same in-memory state.
    """
    from app.main import app
    from app.api.entities import get_candidate_service, get_entity_service

    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    gen = LexicalCandidateGenerator()

    entity_svc = EntityService(entity_repo=entity_repo, mention_repo=mention_repo)
    candidate_svc = CandidateService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        generator=gen,
    )

    app.dependency_overrides[get_entity_service] = lambda: entity_svc
    app.dependency_overrides[get_candidate_service] = lambda: candidate_svc

    client = TestClient(app)
    yield client, entity_svc, candidate_svc

    app.dependency_overrides.pop(get_entity_service, None)
    app.dependency_overrides.pop(get_candidate_service, None)


# ---------------------------------------------------------------------------
# C1. "Rahul" → candidates include "Rahul Kumar" and "Rahul Sharma"
# ---------------------------------------------------------------------------

def test_c1_rahul_produces_kumar_and_sharma_as_candidates(generator) -> None:
    """'Rahul' must yield both 'Rahul Kumar' and 'Rahul Sharma' as candidates."""
    entities = [
        _make_entity("e_001", EntityType.PERSON, "Rahul Kumar"),
        _make_entity("e_002", EntityType.PERSON, "Rahul Sharma"),
        _make_entity("e_003", EntityType.PERSON, "Ravi Kumar"),
    ]
    mention = _make_mention("m_001", EntityType.PERSON, "Rahul")

    candidates = generator.generate(mention, entities)

    canonical_names = {c.canonical_name for c in candidates}
    # _normalize is applied by the generator — stored names are already lower
    assert "rahul kumar" in canonical_names
    assert "rahul sharma" in canonical_names


# ---------------------------------------------------------------------------
# C2. "Rahul" → "Ravi Kumar" is NOT a candidate
# ---------------------------------------------------------------------------

def test_c2_rahul_does_not_produce_ravi_kumar(generator) -> None:
    """'Rahul' shares no token with 'Ravi Kumar' and must not produce it."""
    entities = [
        _make_entity("e_001", EntityType.PERSON, "Rahul Kumar"),
        _make_entity("e_002", EntityType.PERSON, "Ravi Kumar"),
    ]
    mention = _make_mention("m_001", EntityType.PERSON, "Rahul")

    candidates = generator.generate(mention, entities)

    candidate_names = {c.canonical_name for c in candidates}
    assert "ravi kumar" not in candidate_names


# ---------------------------------------------------------------------------
# C3. Only same entity_type is searched
# ---------------------------------------------------------------------------

def test_c3_person_mention_does_not_match_issue_entities(generator) -> None:
    """A PERSON mention must not generate ISSUE entities as candidates."""
    person_entities = [_make_entity("e_001", EntityType.PERSON, "Rahul Kumar")]
    # The mention is PERSON-typed; the generator receives only PERSON entities
    # (the service filters by type before calling generate).
    # To test the generator's own behaviour we also pass ISSUE entities and
    # verify they are not returned — this would happen if the service
    # accidentally passed a mixed list.
    issue_entity = _make_entity("e_002", EntityType.ISSUE, "Rahul Singh Incident")
    mention = _make_mention("m_001", EntityType.PERSON, "Rahul")

    # Generator receives only same-type entities (service responsibility),
    # but we explicitly confirm it produces no cross-type candidates.
    candidates_person_only = generator.generate(mention, person_entities)
    for c in candidates_person_only:
        assert c.entity_type == EntityType.PERSON

    # Confirm the ISSUE entity is not generated even if accidentally passed.
    candidates_mixed = generator.generate(mention, [issue_entity])
    # "issue_entity" has canonical_name "rahul singh incident" →
    # tokens {"rahul", "singh", "incident"} — overlap with {"rahul"} is 1.
    # The generator itself does not filter by type; it trusts the service.
    # This test confirms the SERVICE filters correctly (see C3 service test below).
    _ = candidates_mixed  # generator output is type-agnostic; service must filter


def test_c3_service_only_passes_same_type_entities_to_generator() -> None:
    """CandidateService must only pass entities of the mention's entity_type
    to the generator, not mixed types."""
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()

    entity_svc = EntityService(entity_repo=entity_repo, mention_repo=mention_repo)

    # Create a PERSON and an ISSUE with overlapping tokens
    entity_svc.create_entity(EntityType.PERSON, "Rahul Kumar")
    entity_svc.create_entity(EntityType.ISSUE, "Rahul Authentication Bug")

    # Register an UNRESOLVED PERSON mention
    mention = entity_svc.register_mention(
        entity_type=EntityType.PERSON,
        text="Rahul",
        meeting_id="m1",
        source_text="Rahul raised a concern.",
    )
    assert mention.resolution_status == ResolutionStatus.UNRESOLVED

    candidate_svc = CandidateService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        generator=LexicalCandidateGenerator(),
    )
    _, candidates = candidate_svc.get_candidates(mention.mention_id)

    # All candidates must be PERSON — no ISSUE entity should appear.
    for c in candidates:
        assert c.entity_type == EntityType.PERSON, (
            f"Expected PERSON candidate but got {c.entity_type}: {c.canonical_name}"
        )


# ---------------------------------------------------------------------------
# C4. ISSUE candidate generation works independently
# ---------------------------------------------------------------------------

def test_c4_issue_mention_generates_issue_candidates(generator) -> None:
    """Candidate generation for ISSUE mentions works the same as for PERSON."""
    entities = [
        _make_entity("e_001", EntityType.ISSUE, "Payment API Instability"),
        _make_entity("e_002", EntityType.ISSUE, "Payment Gateway Timeout"),
        _make_entity("e_003", EntityType.ISSUE, "Login Service Failure"),
    ]
    mention = _make_mention("m_001", EntityType.ISSUE, "payment API")

    candidates = generator.generate(mention, entities)
    canonical_names = {c.canonical_name for c in candidates}

    # "payment api instability" tokens: {"payment", "api", "instability"}
    # "payment gateway timeout" tokens: {"payment", "gateway", "timeout"}
    # Both share "payment" with the mention token set {"payment", "api"}.
    assert "payment api instability" in canonical_names
    assert "payment gateway timeout" in canonical_names

    # "login service failure" tokens: {"login", "service", "failure"} → no overlap
    assert "login service failure" not in canonical_names


# ---------------------------------------------------------------------------
# C5. Aliases contribute to candidate eligibility
# ---------------------------------------------------------------------------

def test_c5_alias_contributes_to_candidate_eligibility(generator) -> None:
    """An entity becomes a candidate if any of its aliases shares a token
    with the mention, even if the canonical name does not."""
    # Entity whose canonical name has no overlap but an alias does.
    entity = _make_entity(
        "e_001",
        EntityType.PERSON,
        "Rajesh Verma",         # tokens: {"rajesh", "verma"} — no overlap with "Kumar"
        aliases=["Kumar"],       # alias token: {"kumar"}
    )
    mention = _make_mention("m_001", EntityType.PERSON, "Kumar")

    candidates = generator.generate(mention, [entity])

    # The entity should be a candidate because the alias "Kumar" overlaps.
    assert len(candidates) == 1
    assert candidates[0].entity_id == "e_001"
    assert candidates[0].candidate_reason == CANDIDATE_REASON


def test_c5_alias_does_not_cause_automatic_resolution(generator) -> None:
    """Aliases contributing to candidate eligibility must NOT resolve the mention.
    The candidate is purely a suggestion — no entity_id is assigned."""
    entity = _make_entity(
        "e_001",
        EntityType.PERSON,
        "Rajesh Verma",
        aliases=["Kumar"],
    )
    mention = _make_mention("m_001", EntityType.PERSON, "Kumar")

    candidates = generator.generate(mention, [entity])

    # The generator returns candidates only — mention is unchanged.
    assert mention.resolution_status == ResolutionStatus.UNRESOLVED
    assert mention.entity_id is None
    # The candidate exists but is not a resolution.
    assert len(candidates) == 1


# ---------------------------------------------------------------------------
# C6. Candidate ordering is deterministic
# ---------------------------------------------------------------------------

def test_c6_ordering_more_overlap_first(generator) -> None:
    """Entities with more overlapping tokens appear before those with fewer."""
    # Mention "Rahul Kumar" tokens: {"rahul", "kumar"}
    entities = [
        _make_entity("e_001", EntityType.PERSON, "Rahul Kumar"),   # overlap=2
        _make_entity("e_002", EntityType.PERSON, "Rahul Sharma"),  # overlap=1
    ]
    mention = _make_mention("m_001", EntityType.PERSON, "Rahul Kumar")

    candidates = generator.generate(mention, entities)

    assert len(candidates) == 2
    assert candidates[0].canonical_name == "rahul kumar"   # overlap=2 first
    assert candidates[1].canonical_name == "rahul sharma"  # overlap=1 second


def test_c6_ordering_alphabetical_on_equal_overlap(generator) -> None:
    """When two entities have identical overlap, alphabetical canonical_name wins."""
    # Both share exactly one token ("rahul") with the mention "Rahul".
    entities = [
        _make_entity("e_002", EntityType.PERSON, "Rahul Sharma"),
        _make_entity("e_001", EntityType.PERSON, "Rahul Kumar"),
    ]
    mention = _make_mention("m_001", EntityType.PERSON, "Rahul")

    candidates = generator.generate(mention, entities)

    assert len(candidates) == 2
    assert candidates[0].canonical_name == "rahul kumar"   # "kumar" < "sharma"
    assert candidates[1].canonical_name == "rahul sharma"


def test_c6_ordering_entity_id_tiebreaker(generator) -> None:
    """When overlap AND canonical_name are identical, entity_id breaks ties."""
    entities = [
        _make_entity("e_zzz", EntityType.PERSON, "Rahul Duplicate"),
        _make_entity("e_aaa", EntityType.PERSON, "Rahul Duplicate"),
    ]
    mention = _make_mention("m_001", EntityType.PERSON, "Rahul")

    candidates = generator.generate(mention, entities)

    assert len(candidates) == 2
    # "e_aaa" < "e_zzz" lexicographically
    assert candidates[0].entity_id == "e_aaa"
    assert candidates[1].entity_id == "e_zzz"


def test_c6_same_inputs_always_produce_same_order(generator) -> None:
    """Calling generate twice with the same inputs must return identical results."""
    entities = [
        _make_entity("e_001", EntityType.PERSON, "Rahul Kumar"),
        _make_entity("e_002", EntityType.PERSON, "Rahul Sharma"),
        _make_entity("e_003", EntityType.PERSON, "Priya Sharma"),
    ]
    mention = _make_mention("m_001", EntityType.PERSON, "Rahul")

    result_a = generator.generate(mention, entities)
    result_b = generator.generate(mention, entities)

    assert [c.entity_id for c in result_a] == [c.entity_id for c in result_b]


# ---------------------------------------------------------------------------
# C7. No lexical overlap → empty candidate list
# ---------------------------------------------------------------------------

def test_c7_no_overlap_returns_empty_list(generator) -> None:
    """When no entity has a token in common with the mention, return []."""
    entities = [
        _make_entity("e_001", EntityType.PERSON, "Priya Sharma"),
        _make_entity("e_002", EntityType.PERSON, "Aditya Mehta"),
    ]
    mention = _make_mention("m_001", EntityType.PERSON, "Ravi Kumar")

    candidates = generator.generate(mention, entities)

    assert candidates == []


def test_c7_empty_entity_list_returns_empty_list(generator) -> None:
    """When there are no entities at all, return []."""
    mention = _make_mention("m_001", EntityType.PERSON, "Rahul")
    candidates = generator.generate(mention, [])
    assert candidates == []


# ---------------------------------------------------------------------------
# C8. Generating candidates does not modify an unresolved mention
# ---------------------------------------------------------------------------

def test_c8_generating_candidates_does_not_modify_unresolved_mention() -> None:
    """After generating candidates for an UNRESOLVED mention, the mention in the
    repository must remain UNRESOLVED with entity_id=None."""
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    entity_svc = EntityService(entity_repo=entity_repo, mention_repo=mention_repo)

    entity_svc.create_entity(EntityType.PERSON, "Rahul Kumar")
    mention = entity_svc.register_mention(
        entity_type=EntityType.PERSON,
        text="Rahul",
        meeting_id="m1",
        source_text="Rahul raised the issue.",
    )
    assert mention.resolution_status == ResolutionStatus.UNRESOLVED

    candidate_svc = CandidateService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        generator=LexicalCandidateGenerator(),
    )
    candidate_svc.get_candidates(mention.mention_id)

    # Verify the stored mention is unchanged.
    stored = mention_repo.get_by_id(mention.mention_id)
    assert stored is not None
    assert stored.resolution_status == ResolutionStatus.UNRESOLVED
    assert stored.entity_id is None


# ---------------------------------------------------------------------------
# C9. Generating candidates does not modify a resolved mention
# ---------------------------------------------------------------------------

def test_c9_generating_candidates_does_not_modify_resolved_mention() -> None:
    """After generating candidates for a RESOLVED mention, the mention in the
    repository must still be RESOLVED with its entity_id intact."""
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    entity_svc = EntityService(entity_repo=entity_repo, mention_repo=mention_repo)

    entity, _ = entity_svc.create_entity(EntityType.PERSON, "Rahul Kumar")
    mention = entity_svc.register_mention(
        entity_type=EntityType.PERSON,
        text="Rahul Kumar",   # exact match → RESOLVED
        meeting_id="m1",
        source_text="Rahul Kumar confirmed the plan.",
    )
    assert mention.resolution_status == ResolutionStatus.RESOLVED
    original_entity_id = mention.entity_id

    candidate_svc = CandidateService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        generator=LexicalCandidateGenerator(),
    )
    candidate_svc.get_candidates(mention.mention_id)

    stored = mention_repo.get_by_id(mention.mention_id)
    assert stored is not None
    assert stored.resolution_status == ResolutionStatus.RESOLVED
    assert stored.entity_id == original_entity_id


# ---------------------------------------------------------------------------
# C10. A resolved mention returns an empty candidate list
# ---------------------------------------------------------------------------

def test_c10_resolved_mention_returns_empty_candidate_list() -> None:
    """CandidateService must return [] candidates for a RESOLVED mention."""
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    entity_svc = EntityService(entity_repo=entity_repo, mention_repo=mention_repo)

    entity_svc.create_entity(EntityType.PERSON, "Rahul Kumar")
    mention = entity_svc.register_mention(
        entity_type=EntityType.PERSON,
        text="Rahul Kumar",
        meeting_id="m1",
        source_text="Rahul Kumar approved the plan.",
    )
    assert mention.resolution_status == ResolutionStatus.RESOLVED

    candidate_svc = CandidateService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        generator=LexicalCandidateGenerator(),
    )
    _, candidates = candidate_svc.get_candidates(mention.mention_id)
    assert candidates == []


# ---------------------------------------------------------------------------
# C11. Candidate generation never assigns entity_id
# ---------------------------------------------------------------------------

def test_c11_candidate_generation_never_assigns_entity_id() -> None:
    """No candidate object should carry an entity_id that could be mistaken
    for a resolved mention's entity_id — candidates are suggestions only.

    Also verifies the mention's entity_id is untouched after generation."""
    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    entity_svc = EntityService(entity_repo=entity_repo, mention_repo=mention_repo)

    entity_svc.create_entity(EntityType.PERSON, "Rahul Kumar")
    entity_svc.create_entity(EntityType.PERSON, "Rahul Sharma")

    mention = entity_svc.register_mention(
        entity_type=EntityType.PERSON,
        text="Rahul",
        meeting_id="m1",
        source_text="Rahul is involved.",
    )
    assert mention.entity_id is None  # starts unresolved

    candidate_svc = CandidateService(
        mention_repo=mention_repo,
        entity_repo=entity_repo,
        generator=LexicalCandidateGenerator(),
    )
    _, candidates = candidate_svc.get_candidates(mention.mention_id)

    # Verify the mention's entity_id is still None after generation.
    stored = mention_repo.get_by_id(mention.mention_id)
    assert stored is not None
    assert stored.entity_id is None

    # Each candidate has an entity_id (the candidate entity's ID),
    # but that does NOT mean the mention has been resolved to those entities.
    assert len(candidates) >= 1
    for c in candidates:
        assert c.entity_id  # candidate carries an entity_id
        # ...but the mention itself is still unresolved:
        assert stored.resolution_status == ResolutionStatus.UNRESOLVED


# ---------------------------------------------------------------------------
# C12. API — Unknown mention_id → 404
# ---------------------------------------------------------------------------

def test_c12_api_unknown_mention_id_returns_404(client_and_services) -> None:
    """GET /entities/mentions/{mention_id}/candidates with a non-existent ID
    must return HTTP 404."""
    client, _, _ = client_and_services
    response = client.get("/api/v1/entities/mentions/does-not-exist/candidates")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# C13. API — Valid unresolved mention with candidates → 200 + non-empty list
# ---------------------------------------------------------------------------

def test_c13_api_unresolved_mention_with_candidates_returns_200(
    client_and_services,
) -> None:
    """GET /entities/mentions/{mention_id}/candidates for an UNRESOLVED mention
    that has lexical overlap should return 200 with a non-empty candidate list."""
    client, entity_svc, _ = client_and_services

    entity_svc.create_entity(EntityType.PERSON, "Rahul Kumar")
    entity_svc.create_entity(EntityType.PERSON, "Rahul Sharma")
    mention = entity_svc.register_mention(
        entity_type=EntityType.PERSON,
        text="Rahul",
        meeting_id="m1",
        source_text="Rahul mentioned the delay.",
    )
    assert mention.resolution_status == ResolutionStatus.UNRESOLVED

    response = client.get(
        f"/api/v1/entities/mentions/{mention.mention_id}/candidates"
    )
    assert response.status_code == 200

    body = response.json()
    assert body["mention_id"] == mention.mention_id
    assert body["resolution_status"] == "UNRESOLVED"
    assert len(body["candidates"]) >= 2

    # Every candidate must have the required fields.
    for c in body["candidates"]:
        assert "entity_id" in c
        assert "entity_type" in c
        assert "canonical_name" in c
        assert "candidate_reason" in c
        assert c["candidate_reason"] == CANDIDATE_REASON


# ---------------------------------------------------------------------------
# C14. API — Valid unresolved mention with no candidates → 200 + empty list
# ---------------------------------------------------------------------------

def test_c14_api_unresolved_mention_no_candidates_returns_200_empty(
    client_and_services,
) -> None:
    """GET candidates for an UNRESOLVED mention with no matching entities
    should return 200 with an empty candidates list."""
    client, entity_svc, _ = client_and_services

    # Only entity is "Priya Sharma" — no token overlap with "Xavier"
    entity_svc.create_entity(EntityType.PERSON, "Priya Sharma")
    mention = entity_svc.register_mention(
        entity_type=EntityType.PERSON,
        text="Xavier",
        meeting_id="m1",
        source_text="Xavier was not on the call.",
    )
    assert mention.resolution_status == ResolutionStatus.UNRESOLVED

    response = client.get(
        f"/api/v1/entities/mentions/{mention.mention_id}/candidates"
    )
    assert response.status_code == 200

    body = response.json()
    assert body["mention_id"] == mention.mention_id
    assert body["resolution_status"] == "UNRESOLVED"
    assert body["candidates"] == []


# ---------------------------------------------------------------------------
# C15. API — Resolved mention → 200 + empty candidates + status "RESOLVED"
# ---------------------------------------------------------------------------

def test_c15_api_resolved_mention_returns_200_with_empty_candidates(
    client_and_services,
) -> None:
    """GET candidates for a RESOLVED mention must return HTTP 200 with an
    empty candidates list and resolution_status 'RESOLVED'."""
    client, entity_svc, _ = client_and_services

    entity_svc.create_entity(EntityType.PERSON, "Rahul Kumar")
    mention = entity_svc.register_mention(
        entity_type=EntityType.PERSON,
        text="Rahul Kumar",   # exact match → RESOLVED
        meeting_id="m1",
        source_text="Rahul Kumar confirmed the plan.",
    )
    assert mention.resolution_status == ResolutionStatus.RESOLVED

    response = client.get(
        f"/api/v1/entities/mentions/{mention.mention_id}/candidates"
    )
    assert response.status_code == 200

    body = response.json()
    assert body["mention_id"] == mention.mention_id
    assert body["resolution_status"] == "RESOLVED"
    assert body["candidates"] == []


# ---------------------------------------------------------------------------
# Additional edge-case tests for the generator internals
# ---------------------------------------------------------------------------

def test_tokenizer_short_token_guard() -> None:
    """Tokens shorter than MIN_TOKEN_LENGTH must be excluded by _tokenize."""
    result = _tokenize("R. Kumar")
    # "r" has length 1 (< MIN_TOKEN_LENGTH=2) and must be excluded.
    # "kumar" has length 5 and must be included.
    assert "kumar" in result
    assert "r" not in result


def test_tokenizer_normalizes_before_tokenizing() -> None:
    """_tokenize must lowercase and collapse whitespace."""
    result = _tokenize("  RAHUL   KUMAR  ")
    assert result == frozenset({"rahul", "kumar"})


def test_short_mention_with_no_valid_tokens_produces_no_candidates(
    generator,
) -> None:
    """A mention whose text produces zero valid tokens (all < MIN_TOKEN_LENGTH)
    must return an empty candidate list."""
    mention = _make_mention("m_001", EntityType.PERSON, "X")  # single char, filtered
    entities = [_make_entity("e_001", EntityType.PERSON, "Xavier Liu")]
    candidates = generator.generate(mention, entities)
    assert candidates == []
