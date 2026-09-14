"""Authentication, session, membership, and audit service (Stage 24).

Single home for identity operations so route handlers never implement
security logic themselves:

- user provisioning (bootstrap first owner, member add by admins);
- password verification (PBKDF2-HMAC-SHA256, constant-time, no enumeration);
- opaque server-side sessions (token hashes only, expiry, revocation);
- brute-force protection (per-email failure window → RateLimitedError);
- membership & role changes with last-owner protection;
- security audit events (operational metadata only — never credentials).

Error contract (mapped to HTTP by app/api/auth.py, never here):

- InvalidCredentialsError → 401, generic message for unknown email, wrong
  password, disabled account, expired/revoked session alike;
- RateLimitedError → 429;
- BootstrapClosedError → 403 (first owner already exists);
- OrganisationRequiredError / NotAMemberError → 403;
- LastOwnerError → 409 (refusing to leave an org ownerless).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from app.auth.constants import MIN_PASSWORD_LENGTH, SESSION_TOKEN_BYTES
from app.auth.models import (
    AuthSession,
    MembershipStatus,
    Organisation,
    OrganisationMember,
    OrganisationStatus,
    Role,
    SecurityEvent,
    SecurityEventType,
    User,
    UserStatus,
    normalize_email,
)
from app.auth.passwords import hash_password, hash_token, verify_password
from app.repositories.auth_repositories import (
    AbstractAuthRepository,
    DuplicateUserError,
)

# Verified against when the email does not exist so a missing account costs
# the same PBKDF2 work as a real check (timing-attack hygiene).  The value
# is a fixed, non-functional hash — never a real credential.
_DUMMY_HASH = "pbkdf2-sha256$600000$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

_BANNED_DETAIL_KEYS = ("password", "passwd", "token", "password_hash", "secret")


class AuthError(Exception):
    """Base class for authentication/authorization failures."""


class InvalidCredentialsError(AuthError):
    """Generic login/session failure.  Message is intentionally uniform."""


class RateLimitedError(AuthError):
    """Too many recent failed logins for this identifier."""


class BootstrapClosedError(AuthError):
    """The first owner/organisation already exists."""


class OrganisationRequiredError(AuthError):
    """The user belongs to several organisations; one must be selected."""


class NotAMemberError(AuthError):
    """No active membership for this user/organisation pair."""


class LastOwnerError(AuthError):
    """Refusing an operation that would leave an organisation ownerless."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuthService:
    """Identity boundary.  All timestamps come from the injected clock."""

    def __init__(
        self,
        auth_repository: AbstractAuthRepository,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        session_ttl_seconds: int = 86400,
        pbkdf2_iterations: int = 600000,
        rate_limit_max_attempts: int = 5,
        rate_limit_window_seconds: int = 900,
    ) -> None:
        self._repo = auth_repository
        self._clock = clock or _utcnow
        self._session_ttl = timedelta(seconds=session_ttl_seconds)
        self._pbkdf2_iterations = pbkdf2_iterations
        self._rate_limit_max = rate_limit_max_attempts
        self._rate_limit_window = timedelta(seconds=rate_limit_window_seconds)

    # ------------------------------------------------------------------
    # audit
    # ------------------------------------------------------------------
    def _log_event(
        self,
        event_type: SecurityEventType,
        *,
        user_id: Optional[str] = None,
        organisation_id: Optional[str] = None,
        detail: Optional[dict] = None,
    ) -> SecurityEvent:
        safe = dict(detail or {})
        for key in safe:
            lowered = key.lower()
            if any(banned in lowered for banned in _BANNED_DETAIL_KEYS):
                raise ValueError(f"refusing to audit credential material under key '{key}'")
        return self._repo.record_event(
            SecurityEvent(
                event_id=str(uuid.uuid4()),
                occurred_at=self._clock(),
                event_type=event_type,
                user_id=user_id,
                organisation_id=organisation_id,
                detail=safe,
            )
        )

    # ------------------------------------------------------------------
    # bootstrap — the ONLY open user-creation path
    # ------------------------------------------------------------------
    def bootstrap_first_organisation(
        self, *, organisation_name: str, slug: str, admin_email: str, password: str
    ) -> tuple[User, Organisation, str]:
        """Create the very first organisation + owner + session.

        Open only while NO users exist.  There is no default password and no
        hardcoded admin: the deployer chooses the first credential.
        """
        if self._repo.user_count() != 0:
            raise BootstrapClosedError("bootstrap is closed: users already exist")
        self._require_password_strength(password)
        now = self._clock()
        try:
            user = self._repo.create_user(
                User(
                    user_id=str(uuid.uuid4()), email=admin_email,
                    password_hash=hash_password(password, iterations=self._pbkdf2_iterations),
                    status=UserStatus.ACTIVE, created_at=now, updated_at=now,
                )
            )
        except DuplicateUserError as exc:  # pragma: no cover — count was 0
            raise BootstrapClosedError("bootstrap is closed") from exc
        organisation = self._repo.create_organisation(
            Organisation(
                organisation_id=str(uuid.uuid4()), name=organisation_name,
                slug=slug, status=OrganisationStatus.ACTIVE,
                created_at=now, updated_at=now,
            )
        )
        self._repo.add_member(
            OrganisationMember(
                member_id=str(uuid.uuid4()), organisation_id=organisation.organisation_id,
                user_id=user.user_id, role=Role.OWNER,
                status=MembershipStatus.ACTIVE, created_at=now, updated_at=now,
            )
        )
        token = self._issue_session(user.user_id, now)
        self._log_event(SecurityEventType.ORG_CREATED, user_id=user.user_id,
                        organisation_id=organisation.organisation_id,
                        detail={"slug": organisation.slug})
        self._log_event(SecurityEventType.MEMBER_ADDED, user_id=user.user_id,
                        organisation_id=organisation.organisation_id,
                        detail={"role": Role.OWNER.value, "via": "bootstrap"})
        self._log_event(SecurityEventType.LOGIN_SUCCESS, user_id=user.user_id,
                        organisation_id=organisation.organisation_id,
                        detail={"via": "bootstrap"})
        return user, organisation, token

    # ------------------------------------------------------------------
    # organisation + member provisioning (authenticated paths)
    # ------------------------------------------------------------------
    def create_organisation(
        self, creator_user_id: str, *, name: str, slug: str
    ) -> Organisation:
        creator = self._require_active_user(creator_user_id)
        now = self._clock()
        organisation = self._repo.create_organisation(
            Organisation(
                organisation_id=str(uuid.uuid4()), name=name, slug=slug,
                status=OrganisationStatus.ACTIVE, created_at=now, updated_at=now,
            )
        )
        self._repo.add_member(
            OrganisationMember(
                member_id=str(uuid.uuid4()), organisation_id=organisation.organisation_id,
                user_id=creator.user_id, role=Role.OWNER,
                status=MembershipStatus.ACTIVE, created_at=now, updated_at=now,
            )
        )
        self._log_event(SecurityEventType.ORG_CREATED, user_id=creator.user_id,
                        organisation_id=organisation.organisation_id,
                        detail={"slug": organisation.slug})
        return organisation

    def provision_member(
        self,
        *,
        organisation_id: str,
        email: str,
        role: Role,
        password: Optional[str] = None,
    ) -> tuple[User, bool]:
        """Add an existing user to an org, or create the user then add them.

        Returns (user, created).  A new user requires a password meeting the
        strength rule; an existing user's credential is never changed here.
        """
        organisation = self._repo.get_organisation(organisation_id)
        if organisation is None:
            raise KeyError(organisation_id)
        now = self._clock()
        user = self._repo.get_user_by_email(email)
        created = False
        if user is None:
            if password is None:
                raise ValueError("password is required to create a new user")
            self._require_password_strength(password)
            user = self._repo.create_user(
                User(
                    user_id=str(uuid.uuid4()), email=email,
                    password_hash=hash_password(password, iterations=self._pbkdf2_iterations),
                    status=UserStatus.ACTIVE, created_at=now, updated_at=now,
                )
            )
            created = True
        self._repo.add_member(
            OrganisationMember(
                member_id=str(uuid.uuid4()), organisation_id=organisation_id,
                user_id=user.user_id, role=role,
                status=MembershipStatus.ACTIVE, created_at=now, updated_at=now,
            )
        )
        self._log_event(SecurityEventType.MEMBER_ADDED,
                        user_id=user.user_id, organisation_id=organisation_id,
                        detail={"role": role.value, "created_user": created})
        return user, created

    def change_member_role(
        self, *, organisation_id: str, acting_user_id: str,
        target_user_id: str, role: Role,
    ) -> OrganisationMember:
        acting = self._require_active_membership(organisation_id, acting_user_id)
        if role == Role.OWNER and acting.role != Role.OWNER:
            raise NotAMemberError("only an OWNER may grant the OWNER role")
        target = self._require_active_membership(organisation_id, target_user_id)
        if target.role == Role.OWNER and role != Role.OWNER:
            self._ensure_not_last_owner(organisation_id, target_user_id)
        updated = self._repo.set_member_role(organisation_id, target_user_id, role)
        self._log_event(SecurityEventType.ROLE_CHANGED, user_id=target_user_id,
                        organisation_id=organisation_id,
                        detail={"from": target.role.value, "to": role.value,
                                "by": acting_user_id})
        return updated

    def remove_member(
        self, *, organisation_id: str, acting_user_id: str, target_user_id: str
    ) -> OrganisationMember:
        self._require_active_membership(organisation_id, acting_user_id)
        target = self._require_active_membership(organisation_id, target_user_id)
        if target.role == Role.OWNER:
            self._ensure_not_last_owner(organisation_id, target_user_id)
        removed = self._repo.remove_member(organisation_id, target_user_id)
        self._log_event(SecurityEventType.MEMBER_REMOVED, user_id=target_user_id,
                        organisation_id=organisation_id, detail={"by": acting_user_id})
        return removed

    def disable_user(self, *, acting_user_id: str, target_user_id: str) -> User:
        target = self._repo.get_user_by_id(target_user_id)
        if target is None:
            raise KeyError(target_user_id)
        now = self._clock()
        updated = target.model_copy(update={"status": UserStatus.DISABLED, "updated_at": now})
        self._repo.update_user(updated)
        self._repo.revoke_sessions_for_user(target_user_id)
        self._log_event(SecurityEventType.USER_DISABLED, user_id=target_user_id,
                        detail={"by": acting_user_id})
        return updated

    def enable_user(self, *, acting_user_id: str, target_user_id: str) -> User:
        target = self._repo.get_user_by_id(target_user_id)
        if target is None:
            raise KeyError(target_user_id)
        updated = target.model_copy(
            update={"status": UserStatus.ACTIVE, "updated_at": self._clock()}
        )
        self._repo.update_user(updated)
        self._log_event(SecurityEventType.USER_ENABLED, user_id=target_user_id,
                        detail={"by": acting_user_id})
        return updated

    # ------------------------------------------------------------------
    # authentication
    # ------------------------------------------------------------------
    def authenticate(self, email: str, password: str) -> tuple[User, str]:
        """Verify credentials and issue a session token.

        Every failure — unknown email, wrong password, disabled account,
        rate-limited identifier — records an attempt.  The error message is
        uniform so callers cannot distinguish "email exists" from "password
        incorrect".
        """
        normalized = normalize_email(email)
        now = self._clock()
        if self._repo.count_recent_failures(normalized, now - self._rate_limit_window) >= self._rate_limit_max:
            self._repo.record_login_attempt(normalized, success=False)
            raise RateLimitedError("too many login attempts; try again later")
        user = self._repo.get_user_by_email(normalized)
        if user is None:
            verify_password(password, _DUMMY_HASH)
            self._repo.record_login_attempt(normalized, success=False)
            self._log_event(SecurityEventType.LOGIN_FAILURE, detail={"reason": "unknown"})
            raise InvalidCredentialsError("invalid email or password")
        ok = user.status == UserStatus.ACTIVE and verify_password(password, user.password_hash)
        if not ok:
            self._repo.record_login_attempt(normalized, success=False)
            self._log_event(
                SecurityEventType.LOGIN_FAILURE, user_id=user.user_id,
                detail={"reason": "disabled" if user.status != UserStatus.ACTIVE else "bad_password"},
            )
            raise InvalidCredentialsError("invalid email or password")
        self._repo.record_login_attempt(normalized, success=True)
        token = self._issue_session(user.user_id, now)
        self._log_event(SecurityEventType.LOGIN_SUCCESS, user_id=user.user_id)
        return user, token

    def validate_token(self, token: str) -> tuple[User, AuthSession]:
        """Resolve a bearer token to its user, or raise InvalidCredentialsError."""
        session = self._repo.get_session_by_token_hash(hash_token(token)) if token else None
        if session is None or session.revoked_at is not None:
            raise InvalidCredentialsError("invalid or expired session")
        if session.expires_at is not None and session.expires_at <= self._clock():
            raise InvalidCredentialsError("invalid or expired session")
        user = self._repo.get_user_by_id(session.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise InvalidCredentialsError("invalid or expired session")
        return user, session

    def logout(self, token: str) -> None:
        """Revoke the session behind a token.  Idempotent: unknown tokens are
        accepted silently so logout never oracles session validity."""
        session = self._repo.get_session_by_token_hash(hash_token(token)) if token else None
        if session is None:
            return
        self._repo.revoke_session(session.session_id)
        self._log_event(SecurityEventType.LOGOUT, user_id=session.user_id)

    # ------------------------------------------------------------------
    # membership resolution
    # ------------------------------------------------------------------
    def active_memberships(self, user_id: str) -> list[OrganisationMember]:
        return self._repo.list_memberships_by_user(user_id, active_only=True)

    def resolve_organisation(
        self, user: User, requested_organisation_id: Optional[str]
    ) -> tuple[Organisation, Role]:
        """Map (user, optional client-supplied org) to an authorized scope.

        A client-supplied organisation_id is NEVER trusted: it must match an
        ACTIVE membership, otherwise NotAMemberError.  Without a selection,
        a single membership wins; zero or several require explicit selection.
        """
        memberships = self.active_memberships(user.user_id)
        if requested_organisation_id is not None:
            for membership in memberships:
                if membership.organisation_id == requested_organisation_id:
                    organisation = self._repo.get_organisation(requested_organisation_id)
                    if organisation is None or organisation.status.value != "ACTIVE":
                        raise NotAMemberError("organisation is not available")
                    return organisation, membership.role
            raise NotAMemberError("no access to the requested organisation")
        if len(memberships) == 1:
            membership = memberships[0]
            organisation = self._repo.get_organisation(membership.organisation_id)
            if organisation is None or organisation.status.value != "ACTIVE":
                raise NotAMemberError("organisation is not available")
            return organisation, membership.role
        if not memberships:
            raise NotAMemberError("user has no organisation membership")
        raise OrganisationRequiredError(
            "user belongs to several organisations; select one via X-Organisation-ID"
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _issue_session(self, user_id: str, now: datetime) -> str:
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        self._repo.create_session(
            AuthSession(
                session_id=str(uuid.uuid4()), token_hash=hash_token(token),
                user_id=user_id, created_at=now, expires_at=now + self._session_ttl,
            )
        )
        return token

    def _require_password_strength(self, password: str) -> None:
        if len(password or "") < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")

    def _require_active_user(self, user_id: str) -> User:
        user = self._repo.get_user_by_id(user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise NotAMemberError("user is not available")
        return user

    def _require_active_membership(
        self, organisation_id: str, user_id: str
    ) -> OrganisationMember:
        from app.auth.models import MembershipStatus

        membership = self._repo.get_membership(organisation_id, user_id)
        if membership is None or membership.status != MembershipStatus.ACTIVE:
            raise NotAMemberError("no active membership in this organisation")
        return membership

    def _ensure_not_last_owner(self, organisation_id: str, exclude_user_id: str) -> None:
        remaining_owners = [
            m for m in self._repo.list_memberships_by_org(organisation_id, active_only=True)
            if m.role == Role.OWNER and m.user_id != exclude_user_id
        ]
        if not remaining_owners:
            raise LastOwnerError("cannot remove the last OWNER of an organisation")
