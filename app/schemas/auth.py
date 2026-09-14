"""Authentication and organisation API schemas (Stage 24).

Request/response translation only.  Responses NEVER carry password hashes,
tokens, or any credential material.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.auth.constants import MIN_PASSWORD_LENGTH
from app.auth.models import Role


def _non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


class BootstrapRequest(BaseModel):
    """First-run bootstrap: create the first organisation + owner.

    Open ONLY while no users exist.  There is no default password.
    """

    organisation_name: str = Field(..., description="Display name of the first organisation.")
    slug: str = Field(..., description="URL-safe organisation handle, e.g. 'acme'.")
    admin_email: str = Field(..., description="Login email of the first owner.")
    password: str = Field(..., description="Chosen password of the first owner.")

    @field_validator("organisation_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        return _non_blank(v, "organisation_name")

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        if len(v) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        return v


class LoginRequest(BaseModel):
    email: str = Field(..., description="Login email.")
    password: str = Field(..., description="Password.")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="Opaque bearer token for the Authorization header.")
    token_type: str = Field(default="bearer")
    expires_at: datetime = Field(..., description="UTC expiry of this session.")


class UserResponse(BaseModel):
    user_id: str
    email: str
    status: str
    created_at: datetime


class OrganisationResponse(BaseModel):
    organisation_id: str
    name: str
    slug: str
    status: str
    created_at: datetime


class MembershipResponse(BaseModel):
    organisation_id: str
    user_id: str
    email: Optional[str] = Field(default=None, description="Member email (admin views).")
    role: str
    status: str


class BootstrapResponse(BaseModel):
    user: UserResponse
    organisation: OrganisationResponse
    token: TokenResponse


class CreateOrganisationRequest(BaseModel):
    name: str = Field(..., description="Display name. Need not be unique.")
    slug: str = Field(..., description="URL-safe handle. Globally unique.")

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        return _non_blank(v, "name")


class AddMemberRequest(BaseModel):
    email: str = Field(..., description="Email of the user to add (created if new).")
    role: Role = Field(default=Role.MEMBER, description="OWNER, ADMIN, or MEMBER.")
    password: Optional[str] = Field(
        default=None,
        description="Required when the email is not yet registered.",
    )

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        return v


class ChangeRoleRequest(BaseModel):
    role: Role = Field(..., description="New role for the member.")


class RenameOrganisationRequest(BaseModel):
    name: str = Field(..., description="New display name.")

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        return _non_blank(v, "name")


class OrganisationMembershipView(BaseModel):
    organisation: OrganisationResponse
    role: str = Field(..., description="Caller role in this organisation.")
    status: str = Field(..., description="Membership status.")


class MeResponse(BaseModel):
    user: UserResponse
    memberships: list[OrganisationMembershipView] = Field(default_factory=list)
