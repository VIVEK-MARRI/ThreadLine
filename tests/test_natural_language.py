"""Tests for Natural Language Intelligence (Stage 18)."""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.entity import CanonicalEntity, EntityType
from app.models.natural_language import (
    EntityResolutionStatus,
    EvidenceType,
    QueryIntent,
)
from app.services.query_entity_resolver import QueryEntityResolver
from app.services.query_intent_service import QueryIntentService
from app.repositories.entity_repository import InMemoryEntityRepository


client = TestClient(app)


def test_query_intent_service_classification():
    svc = QueryIntentService()
    
    # Entity-level intents
    assert svc.classify("what does project atlas depend on?") == QueryIntent.ENTITY_DEPENDENCIES
    assert svc.classify("what is blocking the payment gateway") == QueryIntent.ENTITY_DEPENDENCIES
    assert svc.classify("what impacts the auth service") == QueryIntent.ENTITY_IMPACTS
    assert svc.classify("what are the risks for project x") == QueryIntent.ENTITY_RISKS
    assert svc.classify("what happened to issue 123") == QueryIntent.ENTITY_HISTORY
    assert svc.classify("what should we do about the database") == QueryIntent.ENTITY_ACTIONS
    assert svc.classify("what changed with the api") == QueryIntent.ENTITY_CHANGES
    assert svc.classify("what is the status of the migration") == QueryIntent.ENTITY_STATUS
    
    # Org-level intents
    assert svc.classify("what are the biggest risks") == QueryIntent.ORGANISATION_RISKS
    assert svc.classify("what changed this week") == QueryIntent.ORGANISATION_CHANGES
    assert svc.classify("what needs attention") == QueryIntent.ORGANISATION_PRIORITIES
    
    # Unknown
    assert svc.classify("should we fire alice") == QueryIntent.UNKNOWN


def test_query_entity_resolver_resolved():
    repo = InMemoryEntityRepository()
    repo.create(CanonicalEntity(
        entity_id="e1",
        entity_type=EntityType.PERSON,
        canonical_name="Alice Smith",
        aliases=["Alice"],
        created_at=datetime.now(timezone.utc)
    ))
    
    svc = QueryEntityResolver(repo)
    
    res = svc.resolve("what is the status of alice?")
    assert res.status == EntityResolutionStatus.RESOLVED
    assert res.entity_id == "e1"
    
    res = svc.resolve("what are the risks for alice smith")
    assert res.status == EntityResolutionStatus.RESOLVED
    assert res.entity_id == "e1"


def test_query_entity_resolver_unresolved():
    repo = InMemoryEntityRepository()
    svc = QueryEntityResolver(repo)
    
    res = svc.resolve("what is the status of bob?")
    assert res.status == EntityResolutionStatus.UNRESOLVED
    assert res.entity_id is None


def test_query_entity_resolver_ambiguous():
    repo = InMemoryEntityRepository()
    repo.create(CanonicalEntity(
        entity_id="e1",
        entity_type=EntityType.PERSON,
        canonical_name="Alice Smith",
        aliases=["Alice"],
        created_at=datetime.now(timezone.utc)
    ))
    repo.create(CanonicalEntity(
        entity_id="e2",
        entity_type=EntityType.PERSON,
        canonical_name="Alice Jones",
        aliases=["Alice"],
        created_at=datetime.now(timezone.utc)
    ))
    
    svc = QueryEntityResolver(repo)
    
    res = svc.resolve("what is the status of alice?")
    assert res.status == EntityResolutionStatus.AMBIGUOUS
    assert res.entity_id is None
    assert set(res.candidates) == {"e1", "e2"}


def test_api_query_unknown_intent():
    resp = client.post("/api/v1/query", json={"question": "should we fire alice?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == QueryIntent.UNKNOWN.value
    assert data["insufficient_evidence"] is True
    assert "could not understand the intent" in data["answer"]


def test_api_query_unresolved_entity():
    resp = client.post("/api/v1/query", json={"question": "what is the status of non_existent_project?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == QueryIntent.ENTITY_STATUS.value
    assert data["insufficient_evidence"] is True
    assert "could not identify the specific entity" in data["answer"]
    assert len(data["warnings"]) > 0


def test_api_query_org_level_no_evidence():
    # An org-level query with empty repository -> should have no evidence
    resp = client.post("/api/v1/query", json={"question": "what changed this week"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == QueryIntent.ORGANISATION_CHANGES.value
    assert data["insufficient_evidence"] is True


def test_api_query_evidence_only_org_level():
    resp = client.post("/api/v1/query/evidence", json={"question": "what changed this week"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == QueryIntent.ORGANISATION_CHANGES.value
    assert data["evidence"] == []
