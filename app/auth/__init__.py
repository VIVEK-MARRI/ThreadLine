"""Stage 24 authentication / organisation / tenant-isolation boundary.

Security layering
-----------------
IDENTITY → AUTHENTICATION → ORGANISATION → AUTHORIZATION → TENANT SCOPE
→ DOMAIN SERVICE → DURABLE DATA → WORKER → SEMANTIC INDEX → QUERY.

Rules enforced by this package:

- Passwords are verified with PBKDF2-HMAC-SHA256 (stdlib ``hashlib``).
  No custom cryptography, no plaintext storage, no credential logging.
- Access tokens are opaque ``secrets.token_urlsafe`` values.  Only the
  SHA-256 hash of a token is persisted, so a database read never yields a
  usable credential.  Sessions are server-side rows with expiry and
  revocation (logout, disable).
- Tenant scope is a string ``organisation_id`` carried on every durable
  row.  Pre-tenant / legacy rows live in the explicit bootstrap
  organisation ``DEFAULT_ORGANISATION_ID`` — never silently assigned to a
  real user.
- Roles are a finite enum (OWNER / ADMIN / MEMBER); permissions are a
  finite enum centralised in ``ROLE_PERMISSIONS``.  Route handlers declare
  the permission they need; the policy lives here, not in every endpoint.
"""

from app.auth.constants import (
    DEFAULT_ORGANISATION_ID,
    DEFAULT_ORGANISATION_NAME,
    MIN_PASSWORD_LENGTH,
    SESSION_TOKEN_BYTES,
)
from app.auth.models import (
    ROLE_PERMISSIONS,
    AuthSession,
    MembershipStatus,
    Organisation,
    OrganisationMember,
    OrganisationStatus,
    Permission,
    Role,
    SecurityEvent,
    SecurityEventType,
    User,
    UserStatus,
)

__all__ = [
    "DEFAULT_ORGANISATION_ID",
    "DEFAULT_ORGANISATION_NAME",
    "MIN_PASSWORD_LENGTH",
    "SESSION_TOKEN_BYTES",
    "ROLE_PERMISSIONS",
    "AuthSession",
    "MembershipStatus",
    "Organisation",
    "OrganisationMember",
    "OrganisationStatus",
    "Permission",
    "Role",
    "SecurityEvent",
    "SecurityEventType",
    "User",
    "UserStatus",
]
