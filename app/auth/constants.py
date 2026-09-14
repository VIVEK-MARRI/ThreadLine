"""Shared constants for the Stage 24 security boundary."""

# Explicit bootstrap organisation that owns every pre-tenant / legacy row.
# This is NOT silently assigned to a user: it is the documented migration
# home for data created before tenancy existed.  Real organisations always
# receive their own generated IDs.
DEFAULT_ORGANISATION_ID = "default"
DEFAULT_ORGANISATION_NAME = "Default organisation"

# Minimum accepted password length.  Strength comes from PBKDF2-HMAC-SHA256
# with a per-user random salt (see app/auth/passwords.py), not from complex
# composition rules.
MIN_PASSWORD_LENGTH = 8

# Entropy for opaque session tokens (secrets.token_urlsafe bytes).
SESSION_TOKEN_BYTES = 32

# HTTP header carrying the session token.
AUTH_HEADER = "Authorization"
AUTH_SCHEME = "Bearer"

# HTTP header optionally carrying the requested organisation scope.  The
# value is ALWAYS checked against actual membership — never trusted.
ORGANISATION_HEADER = "X-Organisation-ID"
