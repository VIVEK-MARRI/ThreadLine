"""Durable identity, organisation, membership, session, and audit models.

Tenancy rule: every tenant-scoped domain row carries ``organisation_id``.
Identity rows (users) are global; access to an organisation is granted only
through an ACTIVE ``OrganisationMember`` row.  There is no
``organisation_id`` column on ``User`` — multi-organisation membership is a
first-class concept.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class UserStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class OrganisationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class MembershipStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"


class Role(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class Permission(str, Enum):
    """Finite permission model.  Route handlers declare one of these; the
    grant table ``ROLE_PERMISSIONS`` below is the single policy source."""

    ORG_VIEW = "ORG_VIEW"
    ORG_MANAGE = "ORG_MANAGE"
    MEMBER_MANAGE = "MEMBER_MANAGE"
    ROLE_MANAGE = "ROLE_MANAGE"
    MEETING_CREATE = "MEETING_CREATE"
    MEETING_READ = "MEETING_READ"
    MEETING_UPDATE = "MEETING_UPDATE"
    ENTITY_READ = "ENTITY_READ"
    ENTITY_MANAGE = "ENTITY_MANAGE"
    QUERY_RUN = "QUERY_RUN"
    INTELLIGENCE_READ = "INTELLIGENCE_READ"
    PROCESSING_RUN = "PROCESSING_RUN"
    JOB_READ = "JOB_READ"
    DIAGNOSTICS_READ = "DIAGNOSTICS_READ"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.ADMIN: frozenset(p for p in Permission if p != Permission.ORG_MANAGE),
    Role.MEMBER: frozenset(
        {
            Permission.ORG_VIEW,
            Permission.MEETING_CREATE,
            Permission.MEETING_READ,
            Permission.MEETING_UPDATE,
            Permission.ENTITY_READ,
            Permission.ENTITY_MANAGE,
            Permission.QUERY_RUN,
            Permission.INTELLIGENCE_READ,
            Permission.PROCESSING_RUN,
            Permission.JOB_READ,
        }
    ),
}


def has_permission(role: Role | None, permission: Permission) -> bool:
    """Central authorization predicate.  ``None`` role never authorizes."""
    if role is None:
        return False
    return permission in ROLE_PERMISSIONS[role]


def normalize_email(value: str) -> str:
    return value.strip().lower()


class User(BaseModel):
    user_id: str = Field(..., description="Unique user identifier (UUID).")
    email: str = Field(..., description="Login identifier, unique, case-insensitive.")
    password_hash: str = Field(
        ...,
        description="PBKDF2-HMAC-SHA256 encoded credential. Never logged, never returned.",
    )
    status: UserStatus = Field(
        default=UserStatus.ACTIVE, description="DISABLED users cannot authenticate."
    )
    created_at: datetime = Field(...)
    updated_at: datetime = Field(...)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("email must be a valid login identifier")
        return normalized


class Organisation(BaseModel):
    organisation_id: str = Field(..., description="Unique organisation identifier (UUID).")
    name: str = Field(..., description="Display name. NOT globally unique.")
    slug: str = Field(..., description="URL-safe handle. Globally unique.")
    status: OrganisationStatus = Field(default=OrganisationStatus.ACTIVE)
    created_at: datetime = Field(...)
    updated_at: datetime = Field(...)

    @field_validator("name")
    @classmethod
    def _non_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("organisation name must not be blank")
        return value.strip()

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, value: str) -> str:
        slug = value.strip().lower()
        if not slug or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in slug):
            raise ValueError("slug must be [a-z0-9-_]+")
        return slug


class OrganisationMember(BaseModel):
    member_id: str = Field(..., description="Unique membership row identifier (UUID).")
    organisation_id: str = Field(...)
    user_id: str = Field(...)
    role: Role = Field(default=Role.MEMBER)
    status: MembershipStatus = Field(default=MembershipStatus.ACTIVE)
    created_at: datetime = Field(...)
    updated_at: datetime = Field(...)


class AuthSession(BaseModel):
    session_id: str = Field(..., description="Unique session identifier (UUID).")
    # SHA-256 hex of the opaque bearer token.  The token itself is NEVER
    # persisted: a database read must never yield a usable credential.
    token_hash: str = Field(...)
    user_id: str = Field(...)
    created_at: datetime = Field(...)
    expires_at: datetime = Field(...)
    revoked_at: Optional[datetime] = Field(
        default=None, description="Set on logout. Revoked sessions never authenticate."
    )


class SecurityEventType(str, Enum):
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILURE = "LOGIN_FAILURE"
    LOGOUT = "LOGOUT"
    USER_DISABLED = "USER_DISABLED"
    USER_ENABLED = "USER_ENABLED"
    ORG_CREATED = "ORG_CREATED"
    MEMBER_ADDED = "MEMBER_ADDED"
    MEMBER_REMOVED = "MEMBER_REMOVED"
    ROLE_CHANGED = "ROLE_CHANGED"


class SecurityEvent(BaseModel):
    event_id: str = Field(...)
    occurred_at: datetime = Field(...)
    event_type: SecurityEventType = Field(...)
    user_id: Optional[str] = Field(default=None)
    organisation_id: Optional[str] = Field(default=None)
    # Operational metadata only.  MUST never contain passwords, tokens, or
    # password hashes — enforced by construction at the single log call site.
    detail: dict[str, Any] = Field(default_factory=dict)
