"""Frontend contract round-trip: exercise the REAL backend exactly the way
the frontend does, and assert the response shapes the frontend types declare.

Not a unit test — a live cross-check against the current backend implementation
so a backend shape drift breaks this run instead of a silent UI bug.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.auth import get_auth_repository
from app.main import app

NOW = {"users": [], "orgs": []}


def _tag(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
def _isolate():
    # Hermetic: created identities must be removed so bootstrap-open mode is
    # restored for the rest of the suite (same rule as stage 24 security tests).
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    repo = get_auth_repository()
    for org_id in NOW["orgs"]:
        try:
            repo.delete_organisation(org_id)
        except Exception:
            pass
    for user_id in NOW["users"]:
        try:
            repo.delete_user(user_id)
        except Exception:
            pass
    NOW["users"].clear()
    NOW["orgs"].clear()


@pytest.fixture()
def client() -> Iterator[TestClient]:
    tc = TestClient(app)
    yield tc


def test_frontend_bootstrap_login_me_entities_contract(client):
    slug = _tag("fe")
    boot = client.post("/api/v1/auth/bootstrap", json={
        "organisation_name": f"Frontend {slug}",
        "slug": slug,
        "admin_email": f"{_tag('admin')}@x.io",
        "password": "strong-password-1",
    })
    assert boot.status_code == 201, boot.text
    data = boot.json()
    NOW["users"].append(data["user"]["user_id"])
    NOW["orgs"].append(data["organisation"]["organisation_id"])

    # BootstrapResponse: user + organisation + token
    assert set(data) == {"user", "organisation", "token"}
    assert set(data["user"]) == {"user_id", "email", "status", "created_at"}
    assert set(data["organisation"]) == {"organisation_id", "name", "slug", "status", "created_at"}
    assert set(data["token"]) == {"access_token", "token_type", "expires_at"}

    # LoginRequest/TokenResponse
    session = client.post("/api/v1/auth/login", json={
        "email": data["user"]["email"],
        "password": "strong-password-1",
    })
    assert session.status_code == 200, session.text
    token = session.json()
    assert "access_token" in token and "expires_at" in token
    headers = {"Authorization": f"Bearer {token['access_token']}"}

    # MeResponse: user + memberships[], each {organisation:{...}, role, status}
    profile = client.get("/api/v1/auth/me", headers=headers)
    assert profile.status_code == 200, profile.text
    me = profile.json()
    assert set(me) == {"user", "memberships"}
    assert set(me["user"]) == {"user_id", "email", "status", "created_at"}
    assert len(me["memberships"]) == 1
    membership = me["memberships"][0]
    assert set(membership["organisation"]) == {
        "organisation_id", "name", "slug", "status", "created_at",
    }
    assert set(membership) == {"organisation", "role", "status"}
    assert membership["role"] in {"OWNER", "ADMIN", "MEMBER"}

    # Entities list — the query the frontend runs for the entities page.
    entities = client.get("/api/v1/entities", headers=headers)
    assert entities.status_code == 200, entities.text
    assert isinstance(entities.json(), list)


def test_dashboard_contract_shapes(client):
    """Dashboard (Stage 26) reads four endpoints; lock their exact envelopes."""
    slug = _tag("dash")
    boot = client.post("/api/v1/auth/bootstrap", json={
        "organisation_name": f"Dashboard {slug}",
        "slug": slug,
        "admin_email": f"{_tag('admin')}@x.io",
        "password": "strong-password-1",
    })
    assert boot.status_code == 201, boot.text
    data = boot.json()
    NOW["users"].append(data["user"]["user_id"])
    NOW["orgs"].append(data["organisation"]["organisation_id"])
    org_id = data["organisation"]["organisation_id"]

    session = client.post("/api/v1/auth/login", json={
        "email": data["user"]["email"],
        "password": "strong-password-1",
    })
    assert session.status_code == 200, session.text
    headers = {
        "Authorization": f"Bearer {session.json()['access_token']}",
        "X-Organisation-ID": org_id,
    }

    # GET /attention → AttentionResponse
    attention = client.get("/api/v1/attention", headers=headers)
    assert attention.status_code == 200, attention.text
    attention_body = attention.json()
    assert set(attention_body) == {"entity_count", "items"}
    assert isinstance(attention_body["items"], list)

    # GET /portfolio → OrganisationPortfolioResponse
    portfolio = client.get("/api/v1/portfolio", headers=headers)
    assert portfolio.status_code == 200, portfolio.text
    portfolio_body = portfolio.json()
    assert set(portfolio_body) == {
        "total_entities", "critical_entities", "high_risk_entities",
        "medium_risk_entities", "low_risk_entities",
        "entities_with_active_actions", "entities_with_impact",
        "blocked_entities", "entities", "evaluated_at",
    }
    assert isinstance(portfolio_body["entities"], list)
    # For a brand-new org with no observations the portfolio is empty but
    # the envelope shape is validated above — the dashboard uses this
    # exact structure for the snapshot line and entity directory.

    # GET /changes (with the dashboard's limit filter) → OrganisationChangesResponse
    changes = client.get("/api/v1/changes", params={"limit": 8}, headers=headers)
    assert changes.status_code == 200, changes.text
    changes_body = changes.json()
    assert set(changes_body) == {
        "total_changes", "critical_changes", "high_changes",
        "medium_changes", "info_changes", "changes", "evaluated_at",
    }
    assert isinstance(changes_body["changes"], list)

    # GET /health/jobs → queue health consumed by the processing strip
    jobs = client.get("/api/v1/health/jobs", headers=headers)
    assert jobs.status_code == 200, jobs.text
    jobs_body = jobs.json()
    assert set(jobs_body) == {
        "worker_enabled", "counts", "stale_running", "oldest_pending_age_seconds",
    }
    assert isinstance(jobs_body["worker_enabled"], bool)
    assert isinstance(jobs_body["counts"], dict)
    assert isinstance(jobs_body["stale_running"], int)
