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