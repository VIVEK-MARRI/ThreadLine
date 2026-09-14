"""Authentication API router plus request authorisation dependencies.

HTTP-only layer: parsing, status codes, response serialisation.  All
identity logic lives in AuthService; all tenant enforcement lives in the
tenant-scoped repositories the resolved context carries.

Authorisation model
-------------------
- ``get_request_context`` authenticates (401) and resolves the organisation
  scope from verified membership (403).  A client-supplied
  ``X-Organisation-ID`` is checked against actual membership — never trusted.
- ``require_permission(p)`` gates data endpoints on the central role policy.
- ``require_org_permission(p)`` gates ``/orgs/{organisation_id}`` endpoints
  on membership in the PATH organisation.
- Bootstrap-open mode: while NO users exist (and
  ``auth_open_bootstrap`` is on), requests run anonymously inside the
  bootstrap organisation so the first owner can be created.  The first user
  permanently ends anonymous access.

Error semantics: 401 unauthenticated, 403 authenticated-but-forbidden,
404 object outside tenant scope (scoped repositories return None).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.constants import AUTH_SCHEME, DEFAULT_ORGANISATION_ID, ORGANISATION_HEADER
from app.auth.models import (
    Organisation,
    OrganisationStatus,
    Permission,
    Role,
    User,
    has_permission,
)
from app.auth.passwords import hash_token  # noqa: F401  (re-export for logout flow clarity)
from app.auth.service import (
    AuthService,
    BootstrapClosedError,
    InvalidCredentialsError,
    LastOwnerError,
    NotAMemberError,
    OrganisationRequiredError,
    RateLimitedError,
)
from app.core.config import settings
from app.repositories.auth_repositories import (
    AbstractAuthRepository,
    DuplicateOrganisationError,
    DuplicateUserError,
    InMemoryAuthRepository,
    SQLiteAuthRepository,
)
from app.repositories.scoped_repositories import TenantRepositories, scope_repositories
from app.schemas.auth import (
    BootstrapRequest,
    BootstrapResponse,
    LoginRequest,
    MembershipResponse,
    OrganisationMembershipView,
    OrganisationResponse,
    TokenResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


# ---------------------------------------------------------------------------
# Singletons (same pattern as the other routers)
# ---------------------------------------------------------------------------

def _build_auth_repository() -> AbstractAuthRepository:
    if settings.source_repository_backend.lower() == "database":
        from app.api.meetings import get_source_store

        store = get_source_store()
        if store is None:
            raise RuntimeError("Database source backend was not initialized")
        return SQLiteAuthRepository(store)
    return InMemoryAuthRepository()


_auth_repository: Optional[AbstractAuthRepository] = None
_auth_service: Optional[AuthService] = None


def get_auth_repository() -> AbstractAuthRepository:
    """Return the shared auth repository singleton (built lazily so router
    import order never matters)."""
    global _auth_repository
    if _auth_repository is None:
        _auth_repository = _build_auth_repository()
    return _auth_repository


def get_auth_service() -> AuthService:
    """Return the shared AuthService singleton."""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService(
            get_auth_repository(),
            session_ttl_seconds=settings.auth_session_ttl_seconds,
            pbkdf2_iterations=settings.auth_pbkdf2_iterations,
            rate_limit_max_attempts=settings.auth_rate_limit_max_attempts,
            rate_limit_window_seconds=settings.auth_rate_limit_window_seconds,
        )
    return _auth_service


# ---------------------------------------------------------------------------
# Request context
# ---------------------------------------------------------------------------

@dataclass
class Authorisation:
    """Resolved (user, organisation, role) plus tenant-scoped repositories."""

    user: Optional[User]
    organisation: Organisation
    organisation_id: str
    role: Optional[Role]
    anonymous: bool
    repos: TenantRepositories


def _tenant_repos(organisation_id: str) -> TenantRepositories:
    # Deferred imports: these routers import this module for the permission
    # dependencies, so importing them at module top would be circular.
    from app.api.entities import _dependency_repository as _entities_deps
    from app.api.entities import _entity_repository as _entities
    from app.api.entities import _mention_repository as _mentions
    from app.api.jobs import get_job_repository
    from app.api.meetings import _extraction_repository as _extractions
    from app.api.meetings import get_meeting_repository
    from app.api.query import _semantic_repository as _semantic

    return scope_repositories(
        organisation_id,
        meetings=get_meeting_repository(),
        extractions=_extractions,
        entities=_entities,
        mentions=_mentions,
        dependencies=_entities_deps,
        jobs=get_job_repository(),
        semantic=_semantic,
    )


def _anonymous_context() -> Authorisation:
    now = datetime.now(timezone.utc)
    organisation = Organisation(
        organisation_id=DEFAULT_ORGANISATION_ID,
        name="Default organisation",
        slug="default",
        status=OrganisationStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    return Authorisation(
        user=None,
        organisation=organisation,
        organisation_id=organisation.organisation_id,
        role=None,
        anonymous=True,
        repos=_tenant_repos(organisation.organisation_id),
    )


def is_bootstrap_open() -> bool:
    """True only while no users exist and open bootstrap is configured."""
    if not settings.auth_open_bootstrap:
        return False
    try:
        return get_auth_repository().user_count() == 0
    except Exception:
        # Fail closed: an unreadable identity store never grants access.
        logger.error("auth user-count check failed; failing closed", exc_info=True)
        return False


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if not token.strip() or scheme.lower() != AUTH_SCHEME.lower():
        return None
    return token.strip()


def get_request_context(request: Request) -> Authorisation:
    """Authenticate the request and resolve its organisation scope."""
    token = _bearer_token(request)
    if token is None:
        if is_bootstrap_open():
            return _anonymous_context()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    try:
        user, _session = get_auth_service().validate_token(token)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired session",
        )
    requested = request.headers.get(ORGANISATION_HEADER)
    requested_org = requested.strip() if requested and requested.strip() else None
    try:
        organisation, role = get_auth_service().resolve_organisation(user, requested_org)
    except NotAMemberError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except OrganisationRequiredError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return Authorisation(
        user=user,
        organisation=organisation,
        organisation_id=organisation.organisation_id,
        role=role,
        anonymous=False,
        repos=_tenant_repos(organisation.organisation_id),
    )


def require_permission(permission: Permission):
    """Dependency factory gating a data endpoint on the central role policy."""

    def _dependency(ctx: Authorisation = Depends(get_request_context)) -> Authorisation:
        if ctx.anonymous:
            # Bootstrap-open mode: the default organisation is being set up.
            return ctx
        if not has_permission(ctx.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{ctx.role.value if ctx.role else None}' lacks permission '{permission.value}'",
            )
        return ctx

    return _dependency


def require_user() -> object:
    """Dependency returning the authenticated user (401 for anonymous)."""

    def _dependency(ctx: Authorisation = Depends(get_request_context)) -> User:
        if ctx.anonymous or ctx.user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        return ctx.user

    return _dependency


def require_org_permission(permission: Permission):
    """Dependency factory for /orgs/{organisation_id} endpoints.

    Membership + permission are evaluated against the PATH organisation, not
    the request-header organisation, so a caller cannot smuggle another
    tenant's scope through the header.
    """

    def _dependency(organisation_id: str, request: Request) -> Authorisation:
        token = _bearer_token(request)
        if token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        try:
            user, _session = get_auth_service().validate_token(token)
        except InvalidCredentialsError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired session",
            )
        service = get_auth_service()
        membership = service._repo.get_membership(organisation_id, user.user_id)
        from app.auth.models import MembershipStatus

        if membership is None or membership.status != MembershipStatus.ACTIVE:
            # Unknown organisation and no-membership share the 403 shape here
            # only when the org exists; unknown IDs are 404 below.  Check
            # existence first so we do not oracle membership for UUIDs.
            if service._repo.get_organisation(organisation_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="organisation not found",
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="no access to this organisation",
            )
        organisation = service._repo.get_organisation(organisation_id)
        if organisation is None or organisation.status != OrganisationStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="organisation not found",
            )
        if not has_permission(membership.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{membership.role.value}' lacks permission '{permission.value}'",
            )
        return Authorisation(
            user=user,
            organisation=organisation,
            organisation_id=organisation.organisation_id,
            role=membership.role,
            anonymous=False,
            repos=_tenant_repos(organisation.organisation_id),
        )

    return _dependency


# ---------------------------------------------------------------------------
# Response translation (never emits credential material)
# ---------------------------------------------------------------------------

def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        user_id=user.user_id,
        email=user.email,
        status=user.status.value,
        created_at=user.created_at,
    )


def _org_to_response(organisation: Organisation) -> OrganisationResponse:
    return OrganisationResponse(
        organisation_id=organisation.organisation_id,
        name=organisation.name,
        slug=organisation.slug,
        status=organisation.status.value,
        created_at=organisation.created_at,
    )


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/bootstrap",
    response_model=BootstrapResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bootstrap the first organisation and owner",
    description=(
        "Open ONLY while no users exist. Creates the first organisation, its "
        "OWNER, and a session token. There is no default password. Once the "
        "first user exists this endpoint is permanently closed (403)."
    ),
)
def bootstrap_first_owner(request: BootstrapRequest) -> BootstrapResponse:
    try:
        user, organisation, token = get_auth_service().bootstrap_first_organisation(
            organisation_name=request.organisation_name,
            slug=request.slug,
            admin_email=request.admin_email,
            password=request.password,
        )
    except BootstrapClosedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except DuplicateOrganisationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except DuplicateUserError as exc:  # pragma: no cover — defensive
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    _user, session = get_auth_service().validate_token(token)
    return BootstrapResponse(
        user=_user_to_response(user),
        organisation=_org_to_response(organisation),
        token=TokenResponse(access_token=token, expires_at=session.expires_at),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate with email and password",
    description=(
        "Returns an opaque bearer session token. Failures share one generic "
        "message; brute-force attempts are throttled with HTTP 429."
    ),
)
def login(request: LoginRequest) -> TokenResponse:
    from app.auth.service import RateLimitedError

    try:
        _user, token = get_auth_service().authenticate(request.email, request.password)
    except RateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        )
    _user, session = get_auth_service().validate_token(token)
    return TokenResponse(access_token=token, expires_at=session.expires_at)


@router.post(
    "/logout",
    summary="Revoke the current session",
    description="Revokes the bearer session. Idempotent: unknown tokens still get 200.",
)
def logout(request: Request) -> dict:
    get_auth_service().logout(_bearer_token(request) or "")
    return {"status": "logged_out"}


@router.get(
    "/me",
    summary="Current user and memberships",
    description="Returns the authenticated user plus their organisation memberships.",
)
def get_me(user: User = Depends(require_user())) -> dict:
    service = get_auth_service()
    views = []
    for membership in service.active_memberships(user.user_id):
        organisation = service._repo.get_organisation(membership.organisation_id)
        if organisation is None:
            continue
        views.append(
            OrganisationMembershipView(
                organisation=_org_to_response(organisation),
                role=membership.role.value,
                status=membership.status.value,
            )
        )
    return {"user": _user_to_response(user), "memberships": views}
