"""Tests for the Entity Resolution Foundation.

All tests are fully deterministic — no LLM calls, no network, no database.

Test coverage
-------------
 1. Create a PERSON entity → 200, correct fields returned.
 2. Create an ISSUE entity → 200, correct fields returned.
 3. Retrieve entity by ID → 200.
 4. Register a mention matching canonical_name exactly → RESOLVED.
 5. Register a mention matching an alias → RESOLVED.
 6. Register "Rahul" when only "Rahul Kumar" exists → UNRESOLVED.
 7. Register "backend lead" → UNRESOLVED.
 8. Case and whitespace normalization: " rahul kumar " == "Rahul Kumar".
 9. Mention entity_id is None when unresolved.
10. Invalid entity ID → 404.
11. List entities with entity_type filter.

Service-layer unit tests (no HTTP):
 S1. EntityService.create_entity returns existing entity on duplicate name.
 S2. EntityService.register_mention resolves via alias.
 S3. EntityService.get_entity raises EntityNotFoundError for unknown id.
 S4. Mention can be stored without resolving to any entity.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.models.entity import EntityType
from app.repositories.entity_repository import InMemoryEntityRepository
from app.repositories.mention_repository import InMemoryMentionRepository
from app.services.entity_service import EntityNotFoundError, EntityService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_service() -> EntityService:
    """Return an EntityService backed by clean in-memory repositories.

    Each test that uses this fixture gets its own isolated service — no
    state leaks between tests.
    """
    return EntityService(
        entity_repo=InMemoryEntityRepository(),
        mention_repo=InMemoryMentionRepository(),
    )


@pytest.fixture()
def client_and_service():
    """Return a (TestClient, EntityService) pair sharing the same repositories.

    Injects the EntityService into the FastAPI app so API tests exercise the
    full stack while still having direct service access for setup.
    """
    from app.main import app
    from app.api.entities import get_entity_service

    entity_repo = InMemoryEntityRepository()
    mention_repo = InMemoryMentionRepository()
    service = EntityService(entity_repo=entity_repo, mention_repo=mention_repo)

    app.dependency_overrides[get_entity_service] = lambda: service
    client = TestClient(app)
    yield client, service
    # Clean up the override after the test
    app.dependency_overrides.pop(get_entity_service, None)


# ---------------------------------------------------------------------------
# 1. Create a PERSON entity
# ---------------------------------------------------------------------------

def test_create_person_entity_returns_correct_fields(client_and_service) -> None:
    """POST /entities with PERSON type should return a valid EntityResponse."""
    client, _ = client_and_service
    response = client.post(
        "/api/v1/entities",
        json={"entity_type": "PERSON", "canonical_name": "Rahul Kumar"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["entity_type"] == "PERSON"
    assert body["canonical_name"] == "rahul kumar"   # normalised to lowercase
    assert "entity_id" in body
    assert len(body["entity_id"]) > 0
    assert "created_at" in body
    assert body["aliases"] == []


# ---------------------------------------------------------------------------
# 2. Create an ISSUE entity
# ---------------------------------------------------------------------------

def test_create_issue_entity_returns_correct_fields(client_and_service) -> None:
    """POST /entities with ISSUE type should return a valid EntityResponse."""
    client, _ = client_and_service
    response = client.post(
        "/api/v1/entities",
        json={"entity_type": "ISSUE", "canonical_name": "Payment API Instability"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["entity_type"] == "ISSUE"
    assert body["canonical_name"] == "payment api instability"
    assert "entity_id" in body


# ---------------------------------------------------------------------------
# 3. Retrieve entity by ID
# ---------------------------------------------------------------------------

def test_get_entity_by_id_returns_200(client_and_service) -> None:
    """GET /entities/{id} should return the previously created entity."""
    client, _ = client_and_service
    post = client.post(
        "/api/v1/entities",
        json={"entity_type": "PERSON", "canonical_name": "Priya Sharma"},
    )
    assert post.status_code == 200
    entity_id = post.json()["entity_id"]

    get = client.get(f"/api/v1/entities/{entity_id}")
    assert get.status_code == 200
    body = get.json()
    assert body["entity_id"] == entity_id
    assert body["canonical_name"] == "priya sharma"
    assert body["entity_type"] == "PERSON"


# ---------------------------------------------------------------------------
# 4. Register a mention matching canonical_name exactly → RESOLVED
# ---------------------------------------------------------------------------

def test_register_mention_exact_canonical_name_resolves(client_and_service) -> None:
    """Registering a mention whose text exactly matches a canonical name (after
    normalisation) should return RESOLVED with the entity's ID."""
    client, service = client_and_service

    # Create entity first
    entity, _ = service.create_entity(EntityType.PERSON, "Rahul Kumar")

    response = client.post(
        "/api/v1/entities/mentions",
        json={
            "entity_type": "PERSON",
            "text": "Rahul Kumar",
            "meeting_id": "meeting_001",
            "source_text": "Rahul Kumar reported the API issue.",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["resolution_status"] == "RESOLVED"
    assert body["entity_id"] == entity.entity_id
    assert body["text"] == "Rahul Kumar"
    assert "mention_id" in body


# ---------------------------------------------------------------------------
# 5. Register a mention matching an alias → RESOLVED
# ---------------------------------------------------------------------------

def test_register_mention_matching_alias_resolves(client_and_service) -> None:
    """Registering a mention whose text matches an alias of a known entity
    should return RESOLVED."""
    client, service = client_and_service

    # Create entity and add alias directly via repository
    entity, _ = service.create_entity(EntityType.PERSON, "Rahul Kumar")
    service._entity_repo.add_alias(entity.entity_id, "Rahul")

    response = client.post(
        "/api/v1/entities/mentions",
        json={
            "entity_type": "PERSON",
            "text": "Rahul",
            "meeting_id": "meeting_001",
            "source_text": "Rahul said the service is down.",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["resolution_status"] == "RESOLVED"
    assert body["entity_id"] == entity.entity_id


# ---------------------------------------------------------------------------
# 6. Register "Rahul" when only "Rahul Kumar" exists → UNRESOLVED
# ---------------------------------------------------------------------------

def test_register_mention_partial_name_is_unresolved(client_and_service) -> None:
    """'Rahul' must NOT resolve to 'Rahul Kumar' — partial/fuzzy matches are
    intentionally not implemented today."""
    client, service = client_and_service
    service.create_entity(EntityType.PERSON, "Rahul Kumar")

    response = client.post(
        "/api/v1/entities/mentions",
        json={
            "entity_type": "PERSON",
            "text": "Rahul",
            "meeting_id": "meeting_001",
            "source_text": "Rahul reported the issue.",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["resolution_status"] == "UNRESOLVED"
    assert body["entity_id"] is None


# ---------------------------------------------------------------------------
# 7. Register "backend lead" → UNRESOLVED
# ---------------------------------------------------------------------------

def test_register_mention_descriptive_text_is_unresolved(client_and_service) -> None:
    """Role descriptions like 'backend lead' must remain UNRESOLVED when no
    entity has that exact canonical name or alias."""
    client, service = client_and_service
    service.create_entity(EntityType.PERSON, "Rahul Kumar")

    response = client.post(
        "/api/v1/entities/mentions",
        json={
            "entity_type": "PERSON",
            "text": "backend lead",
            "meeting_id": "meeting_001",
            "source_text": "The backend lead will own this.",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["resolution_status"] == "UNRESOLVED"
    assert body["entity_id"] is None


# ---------------------------------------------------------------------------
# 8. Case and whitespace normalisation
# ---------------------------------------------------------------------------

def test_normalisation_prevents_duplicate_entity(client_and_service) -> None:
    """Creating 'Rahul Kumar' then ' rahul kumar ' should return the same entity,
    not create two distinct ones."""
    client, _ = client_and_service
    r1 = client.post(
        "/api/v1/entities",
        json={"entity_type": "PERSON", "canonical_name": "Rahul Kumar"},
    )
    r2 = client.post(
        "/api/v1/entities",
        json={"entity_type": "PERSON", "canonical_name": " rahul kumar "},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["entity_id"] == r2.json()["entity_id"]


def test_normalisation_resolves_case_insensitive_mention(client_and_service) -> None:
    """A mention with different casing/whitespace should still resolve to the
    entity if it is an exact match after normalisation."""
    client, service = client_and_service
    entity, _ = service.create_entity(EntityType.PERSON, "Rahul Kumar")

    response = client.post(
        "/api/v1/entities/mentions",
        json={
            "entity_type": "PERSON",
            "text": "  RAHUL  KUMAR  ",
            "meeting_id": "meeting_001",
            "source_text": "RAHUL KUMAR confirmed the plan.",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["resolution_status"] == "RESOLVED"
    assert body["entity_id"] == entity.entity_id


# ---------------------------------------------------------------------------
# 9. Unresolved mention has entity_id = None
# ---------------------------------------------------------------------------

def test_unresolved_mention_has_null_entity_id(client_and_service) -> None:
    """When a mention is UNRESOLVED, entity_id in the response must be null."""
    client, _ = client_and_service
    response = client.post(
        "/api/v1/entities/mentions",
        json={
            "entity_type": "PERSON",
            "text": "someone unknown",
            "meeting_id": "meeting_001",
            "source_text": "Someone unknown raised a concern.",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["entity_id"] is None
    assert body["resolution_status"] == "UNRESOLVED"


# ---------------------------------------------------------------------------
# 10. Invalid entity ID → 404
# ---------------------------------------------------------------------------

def test_get_entity_invalid_id_returns_404(client_and_service) -> None:
    """GET /entities/{id} with a non-existent ID must return HTTP 404."""
    client, _ = client_and_service
    response = client.get("/api/v1/entities/does-not-exist-00000")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 11. List entities with entity_type filter
# ---------------------------------------------------------------------------

def test_list_entities_with_type_filter(client_and_service) -> None:
    """GET /entities?entity_type=PERSON should return only PERSON entities."""
    client, service = client_and_service
    service.create_entity(EntityType.PERSON, "Alice")
    service.create_entity(EntityType.PERSON, "Bob")
    service.create_entity(EntityType.ISSUE, "DB Timeout")

    response = client.get("/api/v1/entities?entity_type=PERSON")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    for item in body:
        assert item["entity_type"] == "PERSON"


def test_list_entities_without_filter_returns_all(client_and_service) -> None:
    """GET /entities without filter should return all entities."""
    client, service = client_and_service
    service.create_entity(EntityType.PERSON, "Alice")
    service.create_entity(EntityType.ISSUE, "DB Timeout")

    response = client.get("/api/v1/entities")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2


# ---------------------------------------------------------------------------
# Service-layer unit tests (S1-S4, no HTTP)
# ---------------------------------------------------------------------------

def test_s1_create_entity_returns_existing_on_duplicate(fresh_service) -> None:
    """EntityService.create_entity must return the existing entity and
    created=False when the normalised name already exists."""
    service = fresh_service
    entity1, created1 = service.create_entity(EntityType.PERSON, "Rahul Kumar")
    assert created1 is True

    entity2, created2 = service.create_entity(EntityType.PERSON, "Rahul Kumar")
    assert created2 is False
    assert entity1.entity_id == entity2.entity_id


def test_s1_same_name_different_type_creates_two_entities(fresh_service) -> None:
    """Entities with the same name but different types are distinct objects."""
    service = fresh_service
    person, _ = service.create_entity(EntityType.PERSON, "Auth")
    issue, _ = service.create_entity(EntityType.ISSUE, "Auth")
    assert person.entity_id != issue.entity_id


def test_s2_register_mention_resolves_via_alias(fresh_service) -> None:
    """EntityService.register_mention must resolve when text matches an alias."""
    service = fresh_service
    entity, _ = service.create_entity(EntityType.PERSON, "Rahul Kumar")
    service._entity_repo.add_alias(entity.entity_id, "R. Kumar")

    mention = service.register_mention(
        entity_type=EntityType.PERSON,
        text="R. Kumar",
        meeting_id="m1",
        source_text="R. Kumar confirmed the fix.",
    )
    assert mention.resolution_status.value == "RESOLVED"
    assert mention.entity_id == entity.entity_id


def test_s3_get_entity_raises_for_unknown_id(fresh_service) -> None:
    """EntityService.get_entity must raise EntityNotFoundError for missing IDs."""
    with pytest.raises(EntityNotFoundError):
        fresh_service.get_entity("does-not-exist")


def test_s4_mention_can_remain_unresolved(fresh_service) -> None:
    """An unresolved mention must be stored with entity_id=None and status=UNRESOLVED."""
    service = fresh_service
    mention = service.register_mention(
        entity_type=EntityType.PERSON,
        text="the VP of Engineering",
        meeting_id="m1",
        source_text="The VP of Engineering approved the plan.",
    )
    assert mention.entity_id is None
    assert mention.resolution_status.value == "UNRESOLVED"
    # The mention is stored and retrievable
    stored = service._mention_repo.get_by_id(mention.mention_id)
    assert stored is not None
    assert stored.mention_id == mention.mention_id


# ---------------------------------------------------------------------------
# Validation edge cases
# ---------------------------------------------------------------------------

def test_create_entity_blank_canonical_name_returns_422(client_and_service) -> None:
    """A blank canonical_name must be rejected with HTTP 422."""
    client, _ = client_and_service
    response = client.post(
        "/api/v1/entities",
        json={"entity_type": "PERSON", "canonical_name": "   "},
    )
    assert response.status_code == 422


def test_create_entity_invalid_type_returns_422(client_and_service) -> None:
    """An unknown entity_type must be rejected with HTTP 422."""
    client, _ = client_and_service
    response = client.post(
        "/api/v1/entities",
        json={"entity_type": "ROBOT", "canonical_name": "HAL 9000"},
    )
    assert response.status_code == 422


def test_register_mention_blank_text_returns_422(client_and_service) -> None:
    """A blank mention text must be rejected with HTTP 422."""
    client, _ = client_and_service
    response = client.post(
        "/api/v1/entities/mentions",
        json={
            "entity_type": "PERSON",
            "text": "   ",
            "meeting_id": "m1",
            "source_text": "Some text.",
        },
    )
    assert response.status_code == 422
