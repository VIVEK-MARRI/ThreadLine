"""Stage 34 Part D: bootstrap-lifecycle regression tests.

Finding: the implementation already guarantees the safe lifecycle at
runtime, so no production code is changed here:

  * AuthService.bootstrap_first_organisation raises BootstrapClosedError
    (→ HTTP 403) unless the durable user count is zero — evaluated per
    call, never cached at startup.
  * app.api.auth.is_bootstrap_open is True only while the
    AUTH_OPEN_BOOTSTRAP flag is on AND no users exist, and fails closed
    when the identity store is unreadable.
  * There is no default username/password anywhere.

These tests lock that lifecycle: open on fresh install, closed the moment
the first owner exists, closed on anonymous data access afterwards, and
re-evaluated from durable state (a restart with an existing owner does not
reopen bootstrap).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api.auth import get_auth_repository, is_bootstrap_open
from app.main import app

_CREATED = {"users": [], "orgs": []}


def _tag(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _restore_open_bootstrap():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    repo = get_auth_repository()
    for org_id in _CREATED["orgs"]:
        try:
            repo.delete_organisation(org_id)
        except Exception:
            pass
    for user_id in _CREATED["users"]:
        try:
            repo.delete_user(user_id)
        except Exception:
            pass
    _CREATED["users"].clear()
    _CREATED["orgs"].clear()
    assert repo.user_count() == 0


def _bootstrap(client, password="strong-password-1"):
    slug = _tag("org")
    email = f"{_tag('admin')}@x.io"
    response = client.post("/api/v1/auth/bootstrap", json={
        "organisation_name": f"Org {slug}",
        "slug": slug,
        "admin_email": email,
        "password": password,
    })
    assert response.status_code == 201, response.text
    data = response.json()
    _CREATED["users"].append(data["user"]["user_id"])
    _CREATED["orgs"].append(data["organisation"]["organisation_id"])
    return data


class TestBootstrapLifecycle:
    def test_bootstrap_open_on_fresh_install(self, client):
        assert is_bootstrap_open() is True

    def test_bootstrap_creates_first_owner(self, client):
        data = _bootstrap(client)
        assert data["user"]["email"]
        assert data["organisation"]["organisation_id"]
        assert data["token"]["access_token"]

    def test_bootstrap_closes_immediately_after_first_owner(self, client):
        _bootstrap(client)
        # No restart involved: closure is effective on the very next call.
        assert is_bootstrap_open() is False

    def test_second_bootstrap_attempt_rejected(self, client):
        _bootstrap(client)
        response = client.post("/api/v1/auth/bootstrap", json={
            "organisation_name": "Second",
            "slug": _tag("org"),
            "admin_email": f"{_tag('admin')}@x.io",
            "password": "strong-password-1",
        })
        assert response.status_code == 403

    def test_anonymous_data_access_denied_once_owner_exists(self, client):
        _bootstrap(client)
        response = client.get("/api/v1/meetings")
        assert response.status_code == 401

    def test_closure_follows_durable_state_not_startup_flag(self, client):
        # Simulate "restart with an existing owner": the predicate must read
        # durable state on every call, so bootstrap stays closed.
        _bootstrap(client)
        assert is_bootstrap_open() is False
        assert is_bootstrap_open() is False
        # And reopening follows durable state too (fresh install again).
        repo = get_auth_repository()
        for org_id in list(_CREATED["orgs"]):
            repo.delete_organisation(org_id)
        for user_id in list(_CREATED["users"]):
            repo.delete_user(user_id)
        _CREATED["orgs"].clear()
        _CREATED["users"].clear()
        assert is_bootstrap_open() is True

    def test_flag_off_closes_bootstrap_even_with_zero_users(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(auth_api.settings, "auth_open_bootstrap", False)
        try:
            assert is_bootstrap_open() is False
        finally:
            monkeypatch.setattr(auth_api.settings, "auth_open_bootstrap", True)

    def test_no_default_credentials_exist(self, client):
        for email in ("admin", "admin@example.com", "root", "test", ""):
            response = client.post("/api/v1/auth/login", json={
                "email": email or "nobody@example.com",
                "password": "password",
            })
            assert response.status_code in (400, 401, 422), response.text
