"""Password hashing for Stage 24 authentication.

Uses PBKDF2-HMAC-SHA256 from the Python standard library (``hashlib``) —
a NIST-approved, standard password-hashing construction.  No third-party
crypto dependency, no invented cryptography.

- Per-user random 16-byte salt from ``secrets``.
- Configurable iteration count (production default 600_000 per OWASP).
- Constant-time verification via ``hmac.compare_digest``.
- Encoded form carries its own algorithm + iteration count so verification
  stays correct if the count is raised later.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2-sha256"
_SALT_BYTES = 16
_DKLEN = 32


def hash_password(password: str, *, iterations: int) -> str:
    """Hash a plaintext password.  The plaintext never leaves this call."""
    if iterations < 1:
        raise ValueError("iterations must be positive")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=_DKLEN
    )
    return (
        f"{ALGORITHM}${iterations}"
        f"${base64.b64encode(salt).decode('ascii')}"
        f"${base64.b64encode(digest).decode('ascii')}"
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time password check.  Returns False on any malformed input."""
    try:
        algorithm, iteration_text, salt_b64, digest_b64 = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        iterations = int(iteration_text)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
    except Exception:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected)
    )
    return hmac.compare_digest(candidate, expected)


def hash_token(token: str) -> str:
    """SHA-256 hex of an opaque session token for persistence."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
