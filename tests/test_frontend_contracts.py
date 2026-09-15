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


def test_entity_workspace_acceptance_contract(client):
    """Exercise the complete entity workspace read path against real backend state."""
    slug = _tag("entity-accept")
    boot = client.post("/api/v1/auth/bootstrap", json={
        "organisation_name": f"Entity {slug}",
        "slug": slug,
        "admin_email": f"{_tag('admin')}@x.io",
        "password": "strong-password-1",
    })
    assert boot.status_code == 201, boot.text
    data = boot.json()
    NOW["users"].append(data["user"]["user_id"])
    NOW["orgs"].append(data["organisation"]["organisation_id"])
    org_id = data["organisation"]["organisation_id"]
    headers = {
        "Authorization": f"Bearer {data['token']['access_token']}",
        "X-Organisation-ID": org_id,
    }

    canonical_name = f"Acceptance Gateway {slug}"
    meeting_id = f"entity-accept-{slug}"
    meeting = client.post("/api/v1/meetings", json={
        "meeting_id": meeting_id,
        "title": f"Acceptance Review {slug}",
        "transcript": f"Sam mentioned {canonical_name.lower()} during acceptance.",
        "meeting_date": "2026-09-10T10:00:00Z",
        "participants": ["Sam"],
    }, headers=headers)
    assert meeting.status_code == 201, meeting.text

    created = client.post("/api/v1/entities", json={
        "entity_type": "ISSUE",
        "canonical_name": canonical_name,
    }, headers=headers)
    assert created.status_code in {200, 201}, created.text
    entity_id = created.json()["entity_id"]
    assert created.json()["canonical_name"] == canonical_name.lower()

    mention = client.post("/api/v1/entities/mentions", json={
        "entity_type": "ISSUE",
        "text": canonical_name.lower(),
        "meeting_id": meeting_id,
        "source_text": f"Sam mentioned {canonical_name.lower()} during acceptance.",
    }, headers=headers)
    assert mention.status_code == 201, mention.text
    assert mention.json()["entity_id"] == entity_id
    assert mention.json()["resolution_status"] == "RESOLVED"

    workspace_paths = [
        f"/api/v1/entities/{entity_id}",
        f"/api/v1/entities/{entity_id}/temporal",
        f"/api/v1/entities/{entity_id}/timeline",
        f"/api/v1/entities/{entity_id}/memory",
        f"/api/v1/entities/{entity_id}/insights",
        f"/api/v1/entities/{entity_id}/attention",
        f"/api/v1/entities/{entity_id}/actions",
        f"/api/v1/entities/{entity_id}/relationships",
        f"/api/v1/entities/{entity_id}/dependencies",
        f"/api/v1/entities/{entity_id}/dependency-graph",
        f"/api/v1/entities/{entity_id}/impacts",
    ]
    for path in workspace_paths:
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)

    temporal = client.get(f"/api/v1/entities/{entity_id}/temporal", headers=headers).json()
    assert temporal["entity_id"] == entity_id
    assert temporal["observation_count"] == 1
    assert temporal["timeline"][0]["meeting_id"] == meeting_id

    memory = client.get(f"/api/v1/entities/{entity_id}/memory", headers=headers).json()
    assert memory["entity_id"] == entity_id
    assert memory["observation_count"] == 1
    assert memory["meeting_count"] == 1

    timeline = client.get(f"/api/v1/entities/{entity_id}/timeline", headers=headers).json()
    assert timeline["entity_id"] == entity_id
    assert timeline["event_count"] >= 1

    listed = client.get("/api/v1/entities", params={"entity_type": "ISSUE"}, headers=headers)
    assert listed.status_code == 200, listed.text
    assert entity_id in {item["entity_id"] for item in listed.json()}
    excluded = client.get("/api/v1/entities", params={"entity_type": "PERSON"}, headers=headers)
    assert excluded.status_code == 200, excluded.text
    assert entity_id not in {item["entity_id"] for item in excluded.json()}

    scoped_changes = client.get("/api/v1/changes", params={"entity_id": entity_id}, headers=headers)
    assert scoped_changes.status_code == 200, scoped_changes.text
    assert set(scoped_changes.json()) == {
        "total_changes", "critical_changes", "high_changes",
        "medium_changes", "info_changes", "changes", "evaluated_at",
    }

    workspace_portfolio = client.get("/api/v1/portfolio", headers=headers)
    assert workspace_portfolio.status_code == 200, workspace_portfolio.text
    assert entity_id in {item["entity_id"] for item in workspace_portfolio.json()["entities"]}

    assert client.get("/api/v1/entities/entity-does-not-exist", headers=headers).status_code == 404
    unknown_org = dict(headers, **{"X-Organisation-ID": f"unknown-{slug}"})
    assert client.get("/api/v1/entities", headers=unknown_org).status_code == 403
    assert client.get(f"/api/v1/entities/{entity_id}", headers=unknown_org).status_code == 403


def test_intelligence_workspace_acceptance_contract(client):
    """Seed real blocked/dependency/repeated intelligence and prove the exact shapes Intelligence uses."""
    slug = _tag("intelligence")
    boot = client.post("/api/v1/auth/bootstrap", json={
        "organisation_name": f"Intelligence {slug}",
        "slug": slug,
        "admin_email": f"{_tag('admin')}@x.io",
        "password": "strong-password-1",
    })
    assert boot.status_code == 201, boot.text
    data = boot.json()
    NOW["users"].append(data["user"]["user_id"])
    NOW["orgs"].append(data["organisation"]["organisation_id"])
    org_id = data["organisation"]["organisation_id"]
    headers = {
        "Authorization": f"Bearer {data['token']['access_token']}",
        "X-Organisation-ID": org_id,
    }

    gateway = f"acceptance gateway {slug}"
    helper = f"acceptance helper {slug}"
    opened_meeting = f"intelligence-opened-{slug}"
    blocked_meeting = f"intelligence-blocked-{slug}"
    for meeting_id, title, meeting_date, transcript in (
        (opened_meeting, f"Opened Review {slug}", "2026-09-10T10:00:00Z", f"Sam opened {gateway}."),
        (blocked_meeting, f"Blocked Review {slug}", "2026-09-12T10:00:00Z", f"Sam discussed {gateway}."),
    ):
        meeting = client.post("/api/v1/meetings", json={
            "meeting_id": meeting_id,
            "title": title,
            "transcript": transcript,
            "meeting_date": meeting_date,
            "participants": ["Sam"],
        }, headers=headers)
        assert meeting.status_code == 201, meeting.text

    created_helper = client.post("/api/v1/entities", json={
        "entity_type": "ISSUE",
        "canonical_name": helper,
    }, headers=headers)
    assert created_helper.status_code in {200, 201}, created_helper.text
    helper_id = created_helper.json()["entity_id"]
    created_gateway = client.post("/api/v1/entities", json={
        "entity_type": "ISSUE",
        "canonical_name": gateway,
    }, headers=headers)
    assert created_gateway.status_code in {200, 201}, created_gateway.text
    gateway_id = created_gateway.json()["entity_id"]

    opened_mention = client.post("/api/v1/entities/mentions", json={
        "entity_type": "ISSUE",
        "text": gateway,
        "meeting_id": opened_meeting,
        "source_text": f"Sam opened {gateway}.",
    }, headers=headers)
    assert opened_mention.status_code == 201, opened_mention.text
    blocked_mention = client.post("/api/v1/entities/mentions", json={
        "entity_type": "ISSUE",
        "text": gateway,
        "meeting_id": blocked_meeting,
        "source_text": f"{gateway} depends on {helper}. The {gateway} is blocked.",
    }, headers=headers)
    assert blocked_mention.status_code == 201, blocked_mention.text
    repeated_mention = client.post("/api/v1/entities/mentions", json={
        "entity_type": "ISSUE",
        "text": gateway,
        "meeting_id": blocked_meeting,
        "source_text": f"Sam repeated {gateway} during acceptance.",
    }, headers=headers)
    assert repeated_mention.status_code == 201, repeated_mention.text

    attention = client.get("/api/v1/attention", headers=headers)
    assert attention.status_code == 200, attention.text
    attention_items = {
        item["entity_id"]: item for item in attention.json()["items"]
    }
    assert attention_items[gateway_id]["attention_level"] == "CRITICAL"
    assert "ENTITY_BLOCKED" in attention_items[gateway_id]["reasons"]

    portfolio = client.get("/api/v1/portfolio", headers=headers)
    assert portfolio.status_code == 200, portfolio.text
    portfolio_entities = {
        item["entity_id"]: item for item in portfolio.json()["entities"]
    }
    assert portfolio_entities[gateway_id]["current_state"] == "BLOCKED"
    assert portfolio_entities[gateway_id]["risk_level"] == "CRITICAL"
    assert portfolio_entities[gateway_id]["action_count"] >= 1
    assert portfolio_entities[helper_id]["canonical_name"] == helper

    blocked_changes = client.get(
        "/api/v1/changes",
        params={"change_type": "STATE_BLOCKED", "limit": 50},
        headers=headers,
    )
    assert blocked_changes.status_code == 200, blocked_changes.text
    assert gateway_id in {change["entity_id"] for change in blocked_changes.json()["changes"]}

    repeated_changes = client.get(
        "/api/v1/changes",
        params={"change_type": "REPEATED_UNRESOLVED", "limit": 25},
        headers=headers,
    )
    assert repeated_changes.status_code == 200, repeated_changes.text
    assert gateway_id in {change["entity_id"] for change in repeated_changes.json()["changes"]}

    dependency_changes = client.get(
        "/api/v1/changes",
        params={"change_type": "NEW_DEPENDENCY", "limit": 50},
        headers=headers,
    )
    assert dependency_changes.status_code == 200, dependency_changes.text
    dependency_rows = dependency_changes.json()["changes"]
    assert gateway_id in {change["entity_id"] for change in dependency_rows}
    assert any(
        gateway_id in change["dependency_path"] and helper_id in change["dependency_path"]
        for change in dependency_rows
        if change["dependency_path"]
    )

    critical_changes = client.get(
        "/api/v1/changes",
        params={"severity": "CRITICAL", "limit": 50},
        headers=headers,
    )
    assert critical_changes.status_code == 200, critical_changes.text
    assert all(
        change["severity"] == "CRITICAL" for change in critical_changes.json()["changes"]
    )
    assert gateway_id in {change["entity_id"] for change in critical_changes.json()["changes"]}

    meetings = client.get("/api/v1/meetings", params={"limit": 100}, headers=headers)
    assert meetings.status_code == 200, meetings.text
    meeting_titles = {
        meeting["meeting_id"]: meeting["title"] for meeting in meetings.json()["meetings"]
    }
    assert meeting_titles[blocked_meeting] == f"Blocked Review {slug}"
