"""Stage 24 security regression tests (§33) — real TestClient + real repos.

Uses the REAL FastAPI app with the REAL in-memory backends (default
settings) and REAL SQLite for migration/restart tests.  No mocked security
boundaries: authentication, membership, roles, and tenant-scoped
repositories are all exercised through HTTP.

Isolation note: the app singletons are process-global.  Every test uses
unique IDs, and the autouse fixture deletes all created users/organisations
after each test so bootstrap-open mode is restored for the rest of the
suite (in particular test_temporal.py / test_unified_timeline.py, which run
after this file and require open mode).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.auth import get_auth_repository
from app.auth.models import Role
from app.auth.service import AuthService, InvalidCredentialsError
from app.main import app
from app.repositories.scoped_repositories import (
    TenantScopeMismatchError,
    scope_repositories,
)

NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)
_CREATED = {"users": [], "orgs": []}


def _tag(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _restore_open_bootstrap():
    # Hermetic dependencies: other files may leak dependency_overrides;
    # security tests must exercise the REAL auth/tenant wiring.
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
    assert repo.user_count() == 0, "identity cleanup failed; bootstrap mode not restored"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bootstrap(client, slug=None, email=None, password="strong-password-1", name=None):
    slug = slug or _tag("org")
    email = email or f"{_tag('admin')}@x.io"
    response = client.post("/api/v1/auth/bootstrap", json={
        "organisation_name": name or f"Org {slug}",
        "slug": slug,
        "admin_email": email,
        "password": password,
    })
    assert response.status_code == 201, response.text
    data = response.json()
    _CREATED["users"].append(data["user"]["user_id"])
    _CREATED["orgs"].append(data["organisation"]["organisation_id"])
    return data


def _login(client, email, password="strong-password-1"):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _org_headers(token, org_id=None):
    headers = _auth(token)
    if org_id is not None:
        headers["X-Organisation-ID"] = org_id
    return headers


def _create_org(client, token, slug=None, name=None):
    slug = slug or _tag("org")
    response = client.post("/api/v1/orgs", json={"name": name or f"Org {slug}", "slug": slug},
                           headers=_auth(token))
    assert response.status_code == 201, response.text
    data = response.json()
    _CREATED["orgs"].append(data["organisation_id"])
    return data


def _add_member(client, owner_token, org_id, email=None, role="MEMBER", password="member-password-1"):
    email = email or f"{_tag('mem')}@x.io"
    response = client.post(f"/api/v1/orgs/{org_id}/members",
                           json={"email": email, "role": role, "password": password},
                           headers=_auth(owner_token))
    assert response.status_code == 201, response.text
    data = response.json()
    _CREATED["users"].append(data["user_id"])
    return data


def _ingest(client, headers, meeting_id, transcript, title="Sync"):
    response = client.post("/api/v1/meetings", json={
        "meeting_id": meeting_id,
        "title": title,
        "transcript": transcript,
        "meeting_date": "2026-01-01T10:00:00Z",
        "participants": [],
    }, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _create_entity(client, headers, name, entity_type="ISSUE"):
    response = client.post("/api/v1/entities", json={
        "entity_type": entity_type, "canonical_name": name,
    }, headers=headers)
    assert response.status_code in {200, 201}, response.text
    return response.json()


# ===========================================================================
# §33.1–4 — authentication surface
# ===========================================================================

def test_s24_unauthenticated_access_rejected(client):
    # Open bootstrap mode serves requests (documented first-run behaviour).
    assert client.get("/api/v1/entities").status_code == 200
    data = _bootstrap(client)
    token = _login(client, data["user"]["email"])
    headers = _auth(token)
    assert client.post("/api/v1/meetings", json={
        "meeting_id": _tag("m"), "title": "T", "transcript": "hello world transcript",
        "meeting_date": "2026-01-01T10:00:00Z"}).status_code == 401
    assert client.get("/api/v1/entities").status_code == 401
    assert client.post("/api/v1/query/evidence", json={"question": "anything at all?"}).status_code == 401
    assert client.get("/api/v1/portfolio").status_code == 401
    assert client.get("/api/v1/changes").status_code == 401
    # ...while the bearer token works.
    assert client.get("/api/v1/entities", headers=headers).status_code == 200


def test_s24_invalid_credentials_share_one_message(client):
    data = _bootstrap(client)
    bad_password = client.post("/api/v1/auth/login", json={
        "email": data["user"]["email"], "password": "wrong-password-1"})
    assert bad_password.status_code == 401
    unknown_email = client.post("/api/v1/auth/login", json={
        "email": f"{_tag('ghost')}@x.io", "password": "wrong-password-1"})
    assert unknown_email.status_code == 401
    # No oracle: identical messages whether the email exists or not.
    assert bad_password.json()["detail"] == unknown_email.json()["detail"]


def test_s24_bootstrap_second_attempt_closed_and_no_default_creds(client):
    _bootstrap(client)
    second = client.post("/api/v1/auth/bootstrap", json={
        "organisation_name": "Evil", "slug": _tag("evil"),
        "admin_email": f"{_tag('evil')}@x.io", "password": "strong-password-1"})
    assert second.status_code == 403
    # No default password exists.
    assert client.post("/api/v1/auth/login", json={
        "email": "admin@threadline.io", "password": "admin"}).status_code == 401


def test_s24_bootstrap_rejects_weak_password(client):
    response = client.post("/api/v1/auth/bootstrap", json={
        "organisation_name": "Weak", "slug": _tag("weak"),
        "admin_email": f"{_tag('w')}@x.io", "password": "short"})
    assert response.status_code == 422
    # Nothing was created; bootstrap is still open.
    assert get_auth_repository().user_count() == 0


def test_s24_login_brute_force_throttled(client):
    data = _bootstrap(client)
    email = data["user"]["email"]
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-1"})
        assert response.status_code == 401
    throttled = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-1"})
    assert throttled.status_code == 429
    # Even the correct password is throttled while the window is hot (no oracle).
    assert client.post("/api/v1/auth/login", json={
        "email": email, "password": "strong-password-1"}).status_code == 429


def test_s24_session_expiry_enforced_at_service_level():
    repo = get_auth_repository()
    clock_state = {"t": NOW}
    svc = AuthService(repo, clock=lambda: clock_state["t"], session_ttl_seconds=60,
                      pbkdf2_iterations=1000)
    user, org, token = svc.bootstrap_first_organisation(
        organisation_name="Exp", slug=_tag("exp"), admin_email=f"{_tag('e')}@x.io",
        password="strong-password-1")
    _CREATED["users"].append(user.user_id)
    _CREATED["orgs"].append(org.organisation_id)
    assert svc.validate_token(token)[0].user_id == user.user_id
    clock_state["t"] = NOW + timedelta(seconds=61)
    with pytest.raises(InvalidCredentialsError):
        svc.validate_token(token)


def test_s24_logout_revokes_session(client):
    data = _bootstrap(client)
    token = _login(client, data["user"]["email"])
    assert client.get("/api/v1/entities", headers=_auth(token)).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=_auth(token)).status_code == 200
    assert client.get("/api/v1/entities", headers=_auth(token)).status_code == 401
    # Logout is idempotent: unknown tokens still get 200 (no oracle).
    assert client.post("/api/v1/auth/logout", headers=_auth("bogus")).status_code == 200


# ===========================================================================
# §33.5–6 — disabled users, authorization role boundaries
# ===========================================================================

def test_s24_disabled_user_cannot_authenticate_and_token_dies(client):
    data = _bootstrap(client)
    owner_token, org_id = data["token"]["access_token"], data["organisation"]["organisation_id"]
    member = _add_member(client, owner_token, org_id)
    member_token = _login(client, member["email"], password="member-password-1")
    assert client.get("/api/v1/entities", headers=_auth(member_token)).status_code == 200
    disable = client.post(f"/api/v1/orgs/{org_id}/users/{member['user_id']}/disable",
                          headers=_auth(owner_token))
    assert disable.status_code == 200
    assert disable.json()["status"] == "DISABLED"
    # Existing session dies immediately; password login fails generically.
    assert client.get("/api/v1/entities", headers=_auth(member_token)).status_code == 401
    failed = client.post("/api/v1/auth/login", json={
        "email": member["email"], "password": "member-password-1"})
    assert failed.status_code == 401
    assert failed.json()["detail"] == "invalid email or password"
    # Re-enable restores access.
    assert client.post(f"/api/v1/orgs/{org_id}/users/{member['user_id']}/enable",
                       headers=_auth(owner_token)).status_code == 200
    assert client.get("/api/v1/entities",
                      headers=_auth(_login(client, member["email"], password="member-password-1"))).status_code == 200


def test_s24_authorization_role_matrix(client):
    data = _bootstrap(client)
    owner_token, org_id = data["token"]["access_token"], data["organisation"]["organisation_id"]
    member = _add_member(client, owner_token, org_id)
    member_token = _login(client, member["email"], password="member-password-1")
    member_headers = _auth(member_token)

    # Member: data operations succeed.
    assert client.get("/api/v1/entities", headers=member_headers).status_code == 200
    assert client.post("/api/v1/query/evidence", json={"question": "status update?"},
                       headers=member_headers).status_code == 200
    member_meeting = client.post("/api/v1/meetings", json={
        "meeting_id": _tag("member-meeting"), "title": "Member Sync",
        "transcript": "Member workspace smoke transcript.",
        "meeting_date": "2026-01-01T10:00:00Z", "participants": []},
        headers=member_headers)
    assert member_meeting.status_code == 201, member_meeting.text
    member_meeting_id = member_meeting.json()["meeting_id"]
    assert client.get("/api/v1/meetings", headers=member_headers).status_code == 200
    assert client.get(f"/api/v1/meetings/{member_meeting_id}", headers=member_headers).status_code == 200
    assert client.get(f"/api/v1/meetings/{member_meeting_id}/extraction", headers=member_headers).status_code == 200
    assert client.get(f"/api/v1/meetings/{member_meeting_id}/processing", headers=member_headers).status_code == 200
    assert client.get(f"/api/v1/meetings/{member_meeting_id}/mentions", headers=member_headers).status_code == 200
    # Refresh extraction is permitted for members; without a provider it may
    # report unavailability, but it must never report forbidden.
    assert client.post(f"/api/v1/meetings/{member_meeting_id}/extract", headers=member_headers).status_code in {200, 503}
    # Member: admin-only operations rejected (but own job counts are visible).
    assert client.post(f"/api/v1/orgs/{org_id}/members",
                       json={"email": f"{_tag('x')}@x.io", "role": "MEMBER", "password": "member-password-1"},
                       headers=member_headers).status_code == 403
    assert client.get(f"/api/v1/orgs/{org_id}/members", headers=member_headers).status_code == 403
    assert client.get("/health/diagnostics", headers=member_headers).status_code == 403
    assert client.get("/api/v1/health/jobs", headers=member_headers).status_code == 200

    # Promote to ADMIN: member management + diagnostics now succeed...
    promoted = client.patch(f"/api/v1/orgs/{org_id}/members/{member['user_id']}",
                            json={"role": "ADMIN"}, headers=_auth(owner_token))
    assert promoted.status_code == 200
    assert client.get(f"/api/v1/orgs/{org_id}/members", headers=member_headers).status_code == 200
    assert client.get("/health/diagnostics", headers=member_headers).status_code == 200
    # ...but ADMIN cannot grant OWNER and cannot touch org management.
    assert client.patch(f"/api/v1/orgs/{org_id}/members/{member['user_id']}",
                        json={"role": "OWNER"}, headers=member_headers).status_code == 403
    assert client.patch(f"/api/v1/orgs/{org_id}", json={"name": "Renamed"},
                        headers=member_headers).status_code == 403
    # OWNER can do both.
    assert client.patch(f"/api/v1/orgs/{org_id}", json={"name": "Renamed"},
                        headers=_auth(owner_token)).status_code == 200


def test_s24_last_owner_protected(client):
    data = _bootstrap(client)
    owner_token, org_id = data["token"]["access_token"], data["organisation"]["organisation_id"]
    owner_id = data["user"]["user_id"]
    # Cannot remove or demote the only owner.
    assert client.delete(f"/api/v1/orgs/{org_id}/members/{owner_id}",
                         headers=_auth(owner_token)).status_code == 409
    assert client.patch(f"/api/v1/orgs/{org_id}/members/{owner_id}", json={"role": "MEMBER"},
                        headers=_auth(owner_token)).status_code == 409
    # With a second owner, the first may step down.
    second = _add_member(client, owner_token, org_id, role="OWNER", password="strong-password-1")
    assert client.patch(f"/api/v1/orgs/{org_id}/members/{owner_id}", json={"role": "MEMBER"},
                        headers=_auth(owner_token)).status_code == 200
    assert second["role"] == "OWNER"


def test_s24_removed_member_loses_access_immediately(client):
    data = _bootstrap(client)
    owner_token, org_id = data["token"]["access_token"], data["organisation"]["organisation_id"]
    member = _add_member(client, owner_token, org_id)
    member_token = _login(client, member["email"], password="member-password-1")
    assert client.get("/api/v1/entities", headers=_auth(member_token)).status_code == 200
    assert client.delete(f"/api/v1/orgs/{org_id}/members/{member['user_id']}",
                         headers=_auth(owner_token)).status_code == 200
    # Same token, immediate revocation of organisation access.
    assert client.get("/api/v1/entities", headers=_auth(member_token)).status_code == 403
    assert client.post("/api/v1/query/evidence", json={"question": "status?"},
                       headers=_auth(member_token)).status_code == 403


# ===========================================================================
# §33.7–9, 13–15 — cross-tenant IDOR
# ===========================================================================

def _two_tenants(client):
    """Two DISJOINT tenants: user-a owns only A, user-b owns only B.

    Both orgs contain an identically named entity + meeting so every
    isolation test exercises genuine name/ID collisions across tenants.
    """
    tenant_a = _bootstrap(client, slug=_tag("alpha"))
    token_a = tenant_a["token"]["access_token"]
    user_a = tenant_a["user"]["user_id"]
    org_a = tenant_a["organisation"]["organisation_id"]
    org_b = _create_org(client, token_a, slug=_tag("beta"))["organisation_id"]
    owner_b = _add_member(client, token_a, org_b, role="OWNER", password="strong-password-1")
    token_b = _login(client, owner_b["email"], password="strong-password-1")
    # user-a leaves B: from here the tenants share no principal.
    left = client.delete(f"/api/v1/orgs/{org_b}/members/{user_a}", headers=_auth(token_b))
    assert left.status_code == 200, left.text
    ha, hb = _org_headers(token_a, org_a), _org_headers(token_b, org_b)

    name = f"Apollo Project {_tag('t')}"
    meeting_a, meeting_b = _tag("meeting-a"), _tag("meeting-b")
    _ingest(client, ha, meeting_a, f"{name} is on track. Alpha depends on Beta.")
    _ingest(client, hb, meeting_b, f"{name} is blocked. Alpha depends on Beta.")
    entity_a = _create_entity(client, ha, name)
    entity_b = _create_entity(client, hb, name)
    for headers, meeting in ((ha, meeting_a), (hb, meeting_b)):
        mention = client.post("/api/v1/entities/mentions", json={
            "entity_type": "ISSUE", "text": name,
            "meeting_id": meeting, "source_text": f"{name} status confirmed.",
        }, headers=headers)
        assert mention.status_code == 201, mention.text
    return {
        "token_a": token_a, "org_a": org_a, "ha": ha,
        "meeting_a": meeting_a, "entity_a": entity_a, "name": name,
        "token_b": token_b, "org_b": org_b, "hb": hb,
        "meeting_b": meeting_b, "entity_b": entity_b,
    }


def test_s24_cross_tenant_meeting_idor_returns_not_found(client):
    t = _two_tenants(client)
    # Every direction: foreign meeting looks non-existent (no 403 oracle).
    assert client.get(f"/api/v1/meetings/{t['meeting_b']}", headers=t["ha"]).status_code == 404
    assert client.get(f"/api/v1/meetings/{t['meeting_a']}", headers=t["hb"]).status_code == 404
    assert client.put(f"/api/v1/meetings/{t['meeting_b']}", json={
        "title": "Hijack", "transcript": "hijacked transcript here",
        "meeting_date": "2026-01-02T10:00:00Z"}, headers=t["ha"]).status_code == 404
    assert client.post(f"/api/v1/meetings/{t['meeting_b']}/extract", headers=t["ha"]).status_code == 404
    # ...while the owning tenant reads fine.
    assert client.get(f"/api/v1/meetings/{t['meeting_a']}", headers=t["ha"]).status_code == 200


def test_s24_meeting_workspace_reads_are_tenant_scoped(client):
    t = _two_tenants(client)
    for headers, own_meeting, foreign_meeting in (
        (t["ha"], t["meeting_a"], t["meeting_b"]),
        (t["hb"], t["meeting_b"], t["meeting_a"]),
    ):
        listed = client.get("/api/v1/meetings", headers=headers)
        assert listed.status_code == 200, listed.text
        listing = listed.json()
        assert set(listing) == {"meetings", "limit", "returned_count", "has_more"}
        listed_ids = [meeting["meeting_id"] for meeting in listing["meetings"]]
        assert own_meeting in listed_ids
        assert foreign_meeting not in listed_ids
        summary = next(
            meeting for meeting in listing["meetings"] if meeting["meeting_id"] == own_meeting
        )
        assert set(summary) == {
            "meeting_id", "title", "meeting_date", "participants", "ingested_at",
            "source_revision", "processing_status", "extraction_revision",
            "extracted_at", "issue_count", "task_count", "decision_count",
            "risk_count", "mention_count", "resolved_entity_count",
        }

        processing = client.get(f"/api/v1/meetings/{own_meeting}/processing", headers=headers)
        assert processing.status_code == 200, processing.text
        assert set(processing.json()) == {
            "meeting_id", "source_revision", "status", "processing_complete",
            "is_current", "extraction_revision", "derived_revision",
            "semantic_revision", "stale_mentions", "worker_enabled",
        }

        extraction = client.get(f"/api/v1/meetings/{own_meeting}/extraction", headers=headers)
        assert extraction.status_code == 200, extraction.text
        assert set(extraction.json()) == {"meeting_id", "has_extraction", "extraction"}

        mentions = client.get(f"/api/v1/meetings/{own_meeting}/mentions", headers=headers)
        assert mentions.status_code == 200, mentions.text
        mention_body = mentions.json()
        assert set(mention_body) == {
            "meeting_id", "mention_count", "resolved_mention_count", "mentions"
        }
        assert mention_body["mention_count"] >= 1
        assert {mention["meeting_id"] for mention in mention_body["mentions"]} == {own_meeting}

        # Foreign workspace reads look non-existent from either direction.
        assert client.get(f"/api/v1/meetings/{foreign_meeting}/processing", headers=headers).status_code == 404
        assert client.get(f"/api/v1/meetings/{foreign_meeting}/extraction", headers=headers).status_code == 404
        assert client.get(f"/api/v1/meetings/{foreign_meeting}/mentions", headers=headers).status_code == 404

    assert client.get("/api/v1/meetings").status_code == 401
    assert client.get(f"/api/v1/meetings/{t['meeting_a']}").status_code == 401
    assert client.get("/api/v1/meetings?limit=0", headers=t["ha"]).status_code == 422


def test_s24_cross_tenant_entity_mention_idor(client):
    t = _two_tenants(client)
    assert client.get(f"/api/v1/entities/{t['entity_b']['entity_id']}", headers=t["ha"]).status_code == 404
    assert client.get(f"/api/v1/entities/{t['entity_a']['entity_id']}", headers=t["hb"]).status_code == 404
    assert client.get(f"/api/v1/entities/{t['entity_b']['entity_id']}/relationships",
                      headers=t["ha"]).status_code == 404
    assert client.get(f"/api/v1/entities/{t['entity_b']['entity_id']}/dependencies",
                      headers=t["ha"]).status_code == 404
    assert client.get(f"/api/v1/entities/{t['entity_b']['entity_id']}/dependency-graph",
                      headers=t["ha"]).status_code == 404
    assert client.get(f"/api/v1/entities/{t['entity_b']['entity_id']}/impacts",
                      headers=t["ha"]).status_code == 404
    # Owner reads its own.
    assert client.get(f"/api/v1/entities/{t['entity_a']['entity_id']}", headers=t["ha"]).status_code == 200


def test_s24_cross_tenant_job_access_scoped(client):
    t = _two_tenants(client)
    counts_a = client.get("/health/diagnostics", headers=t["ha"]).json()["background_job_counts"]
    counts_b = client.get("/health/diagnostics", headers=t["hb"]).json()["background_job_counts"]
    # No worker runs under TestClient: each org sees exactly its own PENDING job.
    assert counts_a.get("PENDING", 0) == 1, counts_a
    assert counts_b.get("PENDING", 0) == 1, counts_b
    jobs_a = client.get("/api/v1/health/jobs", headers=t["ha"]).json()["counts"]
    assert jobs_a.get("PENDING", 0) == 1, jobs_a


def test_s24_organisation_header_spoof_rejected(client):
    t = _two_tenants(client)
    # Tenant A presents Tenant B's organisation id: membership check fails.
    spoofed = {"Authorization": f"Bearer {t['token_a']}", "X-Organisation-ID": t["org_b"]}
    assert client.get("/api/v1/entities", headers=spoofed).status_code == 403
    assert client.get(f"/api/v1/meetings/{t['meeting_a']}", headers=spoofed).status_code == 403
    # Unknown organisation id: same rejection, no distinction.
    assert client.get("/api/v1/entities", headers={
        "Authorization": f"Bearer {t['token_a']}", "X-Organisation-ID": _tag("nope")}).status_code == 403


def test_s24_multi_membership_header_selection(client):
    data = _bootstrap(client)
    token = data["token"]["access_token"]
    org_a = data["organisation"]["organisation_id"]
    org_b = _create_org(client, token)["organisation_id"]
    # Two memberships and no header: explicit selection required.
    assert client.get("/api/v1/entities", headers=_auth(token)).status_code == 403
    assert client.get("/api/v1/entities", headers=_org_headers(token, org_a)).status_code == 200
    assert client.get("/api/v1/entities", headers=_org_headers(token, org_b)).status_code == 200


# ===========================================================================
# §33.10–11, 16–18 — semantic / query / resolution / intelligence isolation
# ===========================================================================

def test_s24_cross_tenant_natural_language_query_isolated(client):
    t = _two_tenants(client)
    name = t["name"]
    response_a = client.post("/api/v1/query/evidence", json={
        "question": f"What is the status of {name}?"}, headers=t["ha"])
    assert response_a.status_code == 200, response_a.text
    response_b = client.post("/api/v1/query/evidence", json={
        "question": f"What is the status of {name}?"}, headers=t["hb"])
    assert response_b.status_code == 200, response_b.text
    evidence_a, evidence_b = response_a.json()["evidence"], response_b.json()["evidence"]
    assert evidence_a, "tenant A must see its own evidence"
    assert evidence_b, "tenant B must see its own evidence"
    meetings_a = {e.get("meeting_id") for e in evidence_a}
    meetings_b = {e.get("meeting_id") for e in evidence_b}
    assert t["meeting_a"] in meetings_a
    assert t["meeting_b"] not in meetings_a, "tenant B meeting leaked into tenant A results"
    assert t["meeting_b"] in meetings_b
    assert t["meeting_a"] not in meetings_b, "tenant A meeting leaked into tenant B results"
    blob_a = " ".join((e.get("summary") or "") + " " + (e.get("source_text") or "") for e in evidence_a)
    assert "blocked" not in blob_a.lower(), "tenant B content leaked into tenant A answer context"


def test_s24_cross_tenant_entity_resolution_isolated(client):
    person = f"Test Persona {_tag('p')}"
    t = _two_tenants(client)
    person_a = _create_entity(client, t["ha"], person, entity_type="PERSON")
    person_b = _create_entity(client, t["hb"], person, entity_type="PERSON")
    assert person_a["entity_id"] != person_b["entity_id"]
    # A mention registered in A resolves to A's entity — never B's.
    mention = client.post("/api/v1/entities/mentions", json={
        "entity_type": "PERSON", "text": person,
        "meeting_id": t["meeting_a"], "source_text": f"{person} confirmed the status.",
    }, headers=t["ha"])
    assert mention.status_code == 201, mention.text
    assert mention.json()["entity_id"] == person_a["entity_id"]


def test_s24_cross_tenant_org_intelligence_isolated(client):
    t = _two_tenants(client)
    for path in ("/api/v1/changes", "/api/v1/changes/summary", "/api/v1/portfolio", "/api/v1/attention"):
        response_a = client.get(path, headers=t["ha"])
        assert response_a.status_code == 200, (path, response_a.text)
        blob = response_a.text
        assert t["meeting_b"] not in blob, f"{path} leaked tenant B meeting"
        assert t["entity_b"]["entity_id"] not in blob, f"{path} leaked tenant B entity"


def test_s24_identical_names_coexist_across_tenants(client):
    t = _two_tenants(client)
    # Same canonical name in both tenants: both creations succeed (no global clash).
    assert t["entity_a"]["canonical_name"] == t["entity_b"]["canonical_name"]
    assert t["entity_a"]["entity_id"] != t["entity_b"]["entity_id"]
    listing_a = client.get("/api/v1/entities", headers=t["ha"]).json()
    listing_b = client.get("/api/v1/entities", headers=t["hb"]).json()
    ids_a = {e["entity_id"] for e in listing_a}
    ids_b = {e["entity_id"] for e in listing_b}
    assert t["entity_a"]["entity_id"] in ids_a
    assert t["entity_b"]["entity_id"] not in ids_a
    assert t["entity_b"]["entity_id"] in ids_b
    assert t["entity_a"]["entity_id"] not in ids_b


# ===========================================================================
# Repository-boundary isolation (§34 invariants 2–3, 9–10)
# ===========================================================================

def test_s24_scoped_repository_views_enforce_isolation():
    from app.repositories.dependency_repository import InMemoryDependencyRepository
    from app.repositories.entity_repository import InMemoryEntityRepository
    from app.repositories.mention_repository import InMemoryMentionRepository

    entities, mentions, deps = (
        InMemoryEntityRepository(), InMemoryMentionRepository(), InMemoryDependencyRepository())
    from app.repositories.extraction_repository import InMemoryExtractionRepository
    from app.repositories.meeting_repository import InMemoryMeetingRepository
    from app.repositories.background_job_repository import InMemoryBackgroundJobRepository
    from app.repositories.semantic_index_repository import InMemorySemanticIndexRepository

    views_a = scope_repositories(
        "org-a", meetings=InMemoryMeetingRepository(), extractions=InMemoryExtractionRepository(),
        entities=entities, mentions=mentions, dependencies=deps,
        jobs=InMemoryBackgroundJobRepository(), semantic=InMemorySemanticIndexRepository())
    views_b = scope_repositories(
        "org-b", meetings=InMemoryMeetingRepository(), extractions=InMemoryExtractionRepository(),
        entities=entities, mentions=mentions, dependencies=deps,
        jobs=InMemoryBackgroundJobRepository(), semantic=InMemorySemanticIndexRepository())

    from app.models.entity import CanonicalEntity, EntityType
    views_a.entities.create(CanonicalEntity(
        entity_id="e-shared", entity_type=EntityType.ISSUE, canonical_name="Shared",
        created_at=NOW))
    assert views_a.entities.get_by_id("e-shared") is not None
    # Same backing store, other scope: invisible.
    assert views_b.entities.get_by_id("e-shared") is None
    assert views_b.entities.list_entities() == []
    assert views_b.entities.find_by_canonical_name("Shared", EntityType.ISSUE) is None
    assert views_a.entities.find_by_canonical_name("Shared", EntityType.ISSUE) is not None
    # Cross-tenant write rejected before any mutation.
    from app.models.entity import EntityMention, ResolutionStatus
    foreign = EntityMention(
        mention_id="mn-x", entity_type=EntityType.ISSUE, text="Shared",
        meeting_id="m-x", source_text="s", entity_id="e-shared",
        resolution_status=ResolutionStatus.RESOLVED, created_at=NOW,
        organisation_id="org-b")
    with pytest.raises(TenantScopeMismatchError):
        views_a.mentions.create(foreign)


def test_s24_worker_rejects_cross_tenant_job():
    """A job for org A targeting org B's meeting must fail permanently."""
    import app.main as main_module
    from app.api.auth import get_auth_repository as _get_auth_repo
    from app.api.jobs import get_job_scheduler
    from app.api.meetings import get_meeting_repository
    from app.auth.models import Organisation, OrganisationMember, User, UserStatus
    from app.models.background_job import BackgroundJobStatus, BackgroundJobType
    from app.models.meeting import Meeting
    from app.services.background_worker_service import PermanentJobError
    from app.services.processing_consistency_service import processing_revision_for

    repo = _get_auth_repo()
    now = datetime.now(timezone.utc)
    # Provision identity directly (no HTTP): owner of org-b with a meeting.
    b_user = repo.create_user(User(user_id=f"u-{_tag('w')}", email=f"{_tag('w')}@x.io",
                                   password_hash="x", status=UserStatus.ACTIVE,
                                   created_at=now, updated_at=now))
    _CREATED["users"].append(b_user.user_id)
    b_org = repo.create_organisation(Organisation(organisation_id=_tag("org-b"), name="B",
                                                  slug=_tag("b"), created_at=now, updated_at=now))
    _CREATED["orgs"].append(b_org.organisation_id)
    repo.add_member(OrganisationMember(member_id=_tag("mm"), organisation_id=b_org.organisation_id,
                                       user_id=b_user.user_id,
                                       role=Role.OWNER, created_at=now, updated_at=now))
    # Meeting owned by org-b in the shared in-memory store.
    meeting_id = _tag("meeting-b")
    get_meeting_repository().save(Meeting(
        meeting_id=meeting_id, title="B", transcript="Alpha depends on Beta.",
        meeting_date=now, ingested_at=now, organisation_id=b_org.organisation_id))

    # Malicious/forged job: claims org-a scope but targets org-b's meeting.
    scheduler = get_job_scheduler()
    job = scheduler.build_job(BackgroundJobType.MEETING_PROCESSING, meeting_id,
                              processing_revision=processing_revision_for(1),
                              organisation_id="org-a-attacker")
    scheduler._repository.enqueue(job)
    claimed = scheduler._repository.claim(job.job_id, "probe-worker", 60)
    assert claimed is not None
    try:
        worker = main_module._build_application_worker()
        with pytest.raises(PermanentJobError):
            worker._handlers[BackgroundJobType.MEETING_PROCESSING](claimed)
    finally:
        try:
            scheduler._repository.transition(
                job.job_id, BackgroundJobStatus.FAILED, owner_id="probe-worker",
                last_error="test cleanup", error_type="PERMANENT")
        except Exception:
            pass
    # The victim meeting was never touched by org-a processing.
    assert get_meeting_repository().get_by_id(meeting_id).organisation_id == b_org.organisation_id


# ===========================================================================
# §28 migration, §32 restart, §24 audit, §30 contracts
# ===========================================================================

def test_s24_migration_v4_to_v5_preserves_data_in_bootstrap_org(tmp_path):
    legacy = tmp_path / "legacy.db"
    # Build a REAL v4-equivalent database with the production DDL: create via
    # an older code path is unavailable, so create with current code, strip
    # the v5 additions, and reset the version marker.  The reopen below then
    # exercises the genuine 4 -> 5 migration.
    from app.persistence.sqlite_store import SCHEMA_VERSION, SQLiteSourceStore

    seed = SQLiteSourceStore(legacy)
    seed._connection.execute(
        "INSERT INTO meetings(meeting_id, meeting_date, payload, source_revision, organisation_id)"
        " VALUES ('m-legacy', '2026-01-01T00:00:00+00:00', '{\"meeting_id\":\"m-legacy\"}', 2, 'default')")
    seed._connection.execute(
        "INSERT INTO entities(entity_id, entity_type, canonical_name, payload, organisation_id)"
        " VALUES ('e-legacy', 'ISSUE', 'Legacy', '{\"entity_id\":\"e-legacy\"}', 'default')")
    seed._connection.commit()
    seed._connection.executescript(
        """
        DROP INDEX IF EXISTS idx_meetings_org;
        DROP INDEX IF EXISTS idx_entities_org_name;
        DROP INDEX IF EXISTS idx_mentions_org_meeting;
        DROP INDEX IF EXISTS idx_mentions_org_entity;
        DROP INDEX IF EXISTS idx_dependencies_org_source;
        DROP INDEX IF EXISTS idx_dependencies_org_target;
        DROP INDEX IF EXISTS idx_dependencies_org_meeting;
        DROP INDEX IF EXISTS idx_jobs_org_status;
        ALTER TABLE meetings DROP COLUMN organisation_id;
        ALTER TABLE extraction_results DROP COLUMN organisation_id;
        ALTER TABLE entities DROP COLUMN organisation_id;
        ALTER TABLE entity_mentions DROP COLUMN organisation_id;
        ALTER TABLE dependencies DROP COLUMN organisation_id;
        ALTER TABLE background_jobs DROP COLUMN organisation_id;
        DROP TABLE organisation_members;
        DROP TABLE auth_sessions;
        DROP TABLE auth_login_attempts;
        DROP TABLE security_events;
        DROP TABLE users;
        DROP TABLE organisations;
        DELETE FROM schema_version WHERE version = 5;
        """
    )
    seed._connection.commit()
    assert seed.migration_version() == 4
    seed.close()

    store = SQLiteSourceStore(legacy)
    try:
        assert store.migration_version() == SCHEMA_VERSION == 5
        meeting_row = store._connection.execute(
            "SELECT organisation_id, source_revision FROM meetings WHERE meeting_id = 'm-legacy'").fetchone()
        assert tuple(meeting_row) == ("default", 2)
        entity_row = store._connection.execute(
            "SELECT organisation_id FROM entities WHERE entity_id = 'e-legacy'").fetchone()
        assert entity_row[0] == "default"
        assert store._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        store.close()


def test_s24_restart_preserves_tenant_identity(tmp_path):
    """Sessions and tenant rows survive a store reopen (durable, not memory)."""
    from app.persistence.sqlite_store import SQLiteSourceStore
    from app.repositories.auth_repositories import SQLiteAuthRepository
    from app.repositories.sqlite_source_repositories import SQLiteMeetingRepository

    clock_state = {"t": NOW}
    clock = lambda: clock_state["t"]
    store = SQLiteSourceStore(tmp_path / "restart.db")
    auth_repo = SQLiteAuthRepository(store, clock=clock)
    svc = AuthService(auth_repo, clock=clock, pbkdf2_iterations=1000)
    user, org, token = svc.bootstrap_first_organisation(
        organisation_name="R", slug=_tag("r"), admin_email=f"{_tag('r')}@x.io",
        password="strong-password-1")
    meetings = SQLiteMeetingRepository(store)
    from app.models.meeting import Meeting
    meetings.save(Meeting(meeting_id="m-restart", title="R", transcript="hello world transcript",
                          meeting_date=NOW, ingested_at=NOW, organisation_id=org.organisation_id))
    store.close()

    reopened = SQLiteSourceStore(tmp_path / "restart.db")
    try:
        svc2 = AuthService(SQLiteAuthRepository(reopened, clock=clock), clock=clock,
                           pbkdf2_iterations=1000)
        assert svc2.validate_token(token)[0].user_id == user.user_id
        meeting = SQLiteMeetingRepository(reopened).get_by_id("m-restart")
        assert meeting is not None and meeting.organisation_id == org.organisation_id
    finally:
        reopened.close()


def test_s24_security_events_recorded_without_secrets(client):
    data = _bootstrap(client)
    owner_token, org_id = data["token"]["access_token"], data["organisation"]["organisation_id"]
    email = data["user"]["email"]
    client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-1"})
    member = _add_member(client, owner_token, org_id)
    client.patch(f"/api/v1/orgs/{org_id}/members/{member['user_id']}", json={"role": "ADMIN"},
                 headers=_auth(owner_token))
    client.post("/api/v1/auth/logout", headers=_auth(owner_token))
    # Org-scoped events carry the organisation; login/logout are user-level
    # (no org is known at credential-check time) and queried by user.
    org_events = get_auth_repository().list_events(organisation_id=org_id)
    org_kinds = {e.event_type.value for e in org_events}
    assert {"ORG_CREATED", "MEMBER_ADDED", "ROLE_CHANGED"} <= org_kinds, org_kinds
    user_events = get_auth_repository().list_events(user_id=data["user"]["user_id"])
    user_kinds = {e.event_type.value for e in user_events}
    assert {"LOGIN_FAILURE", "LOGIN_SUCCESS", "LOGOUT"} <= user_kinds, user_kinds
    blob = " ".join(str(e.detail) for e in org_events + user_events)
    assert "strong-password" not in blob
    assert "access_token" not in blob.lower()


def test_s24_api_contracts_document_auth_and_tenancy(client):
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    for required in ("/api/v1/auth/bootstrap", "/api/v1/auth/login", "/api/v1/auth/logout",
                     "/api/v1/auth/me", "/api/v1/orgs", "/api/v1/orgs/{organisation_id}",
                     "/api/v1/meetings", "/api/v1/query/evidence"):
        assert required in paths, required
