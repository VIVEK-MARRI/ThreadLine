"""Stage 34 Part F: query rate-limit regression tests.

Covers the SlidingWindowRateLimiter unit contract (allow/deny, window
expiry, per-key isolation, bounded memory, concurrency, validation) and
the HTTP contract on POST /api/v1/query (200 under quota, 429 shape +
Retry-After over quota, per-user isolation, window recovery), plus a
guard that login's own per-email throttle is untouched.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import query as query_api
from app.api.auth import get_auth_repository
from app.core.config import settings
from app.main import app
from app.services.rate_limit import SlidingWindowRateLimiter

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
    token = data["token"]["access_token"]
    return data, token


def _add_member(client, owner_token, org_id, password="member-password-1"):
    email = f"{_tag('mem')}@x.io"
    response = client.post(
        f"/api/v1/orgs/{org_id}/members",
        json={"email": email, "role": "MEMBER", "password": password},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    _CREATED["users"].append(data["user_id"])
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _ask(client, token):
    return client.post(
        "/api/v1/query",
        json={"question": "what needs attention?"},
        headers={"Authorization": f"Bearer {token}"},
    )


class _Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now


# ---------------------------------------------------------------------------
# Unit: limiter contract
# ---------------------------------------------------------------------------


class TestSlidingWindow:
    def test_under_limit_allowed(self):
        limiter = SlidingWindowRateLimiter(3, 60.0, clock=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))
        assert all(limiter.check("u")[0] for _ in range(3))

    def test_over_limit_denied_with_retry_after(self):
        limiter = SlidingWindowRateLimiter(2, 60.0, clock=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))
        assert limiter.check("u")[0] is True
        assert limiter.check("u")[0] is True
        allowed, retry_after = limiter.check("u")
        assert allowed is False
        assert retry_after > 0

    def test_window_expiry_readmits(self):
        clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        limiter = SlidingWindowRateLimiter(1, 60.0, clock=clock)
        assert limiter.check("u")[0] is True
        assert limiter.check("u")[0] is False
        clock.now = clock.now + timedelta(seconds=61)
        assert limiter.check("u")[0] is True

    def test_keys_are_isolated(self):
        limiter = SlidingWindowRateLimiter(1, 60.0, clock=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))
        assert limiter.check("a")[0] is True
        assert limiter.check("a")[0] is False
        assert limiter.check("b")[0] is True

    def test_memory_bounded_by_max_keys(self):
        limiter = SlidingWindowRateLimiter(100, 60.0, max_keys=5, clock=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))
        for i in range(50):
            limiter.check(f"user-{i}")
        assert limiter.key_count() <= 5

    def test_concurrent_checks_never_exceed_quota(self):
        limiter = SlidingWindowRateLimiter(10, 60.0, clock=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))
        allowed = []
        lock = threading.Lock()

        def hammer():
            local = 0
            for _ in range(25):
                ok, _ = limiter.check("shared")
                if ok:
                    local += 1
            with lock:
                allowed.append(local)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(allowed) == 10

    def test_invalid_configuration_rejected(self):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(0, 60.0)
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(5, 0)
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(5, 60.0, max_keys=0)


# ---------------------------------------------------------------------------
# HTTP: query endpoint contract
# ---------------------------------------------------------------------------


class TestQueryRateLimitEndpoint:
    def test_requests_under_limit_succeed(self, client, monkeypatch):
        monkeypatch.setattr(settings, "query_rate_limit_requests", 5)
        monkeypatch.setattr(settings, "query_rate_limit_window_seconds", 600.0)
        _, token = _bootstrap(client)
        for _ in range(5):
            response = _ask(client, token)
            assert response.status_code == 200, response.text

    def test_request_over_limit_returns_429_with_retry_after(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(settings, "query_rate_limit_requests", 2)
        monkeypatch.setattr(settings, "query_rate_limit_window_seconds", 600.0)
        _, token = _bootstrap(client)
        assert _ask(client, token).status_code == 200
        assert _ask(client, token).status_code == 200
        denied = _ask(client, token)
        assert denied.status_code == 429
        assert "retry-after" in {k.lower() for k in denied.headers}
        body = denied.json()["detail"]
        assert body["error"] == "rate_limited"
        assert body["retry_after_seconds"] >= 1
        assert "user_id" not in denied.text and "Bearer" not in denied.text

    def test_quota_is_per_user(self, client, monkeypatch):
        monkeypatch.setattr(settings, "query_rate_limit_requests", 1)
        monkeypatch.setattr(settings, "query_rate_limit_window_seconds", 600.0)
        data, owner_token = _bootstrap(client)
        member_token = _add_member(client, owner_token, data["organisation"]["organisation_id"])
        assert _ask(client, owner_token).status_code == 200
        assert _ask(client, owner_token).status_code == 429
        # The member's quota is untouched by the owner's consumption.
        assert _ask(client, member_token).status_code == 200

    def test_quota_recovers_after_window(self, client, monkeypatch):
        monkeypatch.setattr(settings, "query_rate_limit_requests", 1)
        monkeypatch.setattr(settings, "query_rate_limit_window_seconds", 0.2)
        _, token = _bootstrap(client)
        assert _ask(client, token).status_code == 200
        assert _ask(client, token).status_code == 429
        time.sleep(0.35)
        assert _ask(client, token).status_code == 200

    def test_evidence_endpoint_shares_protection(self, client, monkeypatch):
        monkeypatch.setattr(settings, "query_rate_limit_requests", 1)
        monkeypatch.setattr(settings, "query_rate_limit_window_seconds", 600.0)
        _, token = _bootstrap(client)
        first = client.post(
            "/api/v1/query/evidence",
            json={"question": "what needs attention?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/v1/query/evidence",
            json={"question": "what needs attention?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code == 429


class TestLoginThrottleUntouched:
    def test_login_throttle_mechanism_still_wired(self, client):
        # Login's per-email throttle lives in app.auth.service and is
        # independent of the query limiter: wrong passwords record failures
        # (401), and the query limiter module is not consulted.
        data, _ = _bootstrap(client)
        email = data["user"]["email"]
        for _ in range(3):
            response = client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "wrong-password-1"},
            )
            assert response.status_code == 401
        # Login's throttle does not consult the query limiter: it neither
        # imports the limiter module nor reads the query quota settings.
        import pathlib

        auth_service_src = pathlib.Path("app/auth/service.py").read_text(encoding="utf-8")
        assert "services.rate_limit" not in auth_service_src
        assert "SlidingWindow" not in auth_service_src
        assert "query_rate_limit" not in auth_service_src
        # Correct password still works: failures did not lock the account
        # below the login throttle threshold.
        ok = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "strong-password-1"},
        )
        assert ok.status_code == 200
