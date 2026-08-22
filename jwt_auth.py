"""Small, dependency-free HS256 authentication primitives for DEADSAT.

Credentials are deployment configuration, never telemetry data or seeded
application accounts.  The API can issue access and short-lived websocket
tokens from those credentials, while still accepting verified external JWTs.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import secrets
from typing import Any

from history_privacy import Requester


class JWTValidationError(ValueError):
    """A token could not be trusted for authentication."""


class CredentialValidationError(ValueError):
    """A configured username/password pair could not be trusted."""


def _decode(segment: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except Exception as exc:
        raise JWTValidationError("Malformed access token") from exc


def _encode(value: Any) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).rstrip(b"=").decode("ascii")


def issue_jwt(*, subject: str, role: str, permissions: frozenset[str] | set[str] | list[str],
              secret: str, expires_in: int, token_type: str = "access",
              issuer: str = "", audience: str = "", now: int | None = None) -> str:
    """Issue a signed, time-limited token from verified server-side identity."""
    if not secret:
        raise JWTValidationError("JWT authentication is not configured")
    timestamp = int(time.time() if now is None else now)
    claims: dict[str, Any] = {
        "sub": subject, "role": role, "permissions": sorted(permissions),
        "iat": timestamp, "exp": timestamp + expires_in, "typ": token_type,
        "jti": secrets.token_urlsafe(18),
    }
    if issuer:
        claims["iss"] = issuer
    if audience:
        claims["aud"] = audience
    header = _encode({"alg": "HS256", "typ": "JWT"})
    payload = _encode(claims)
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode("utf-8"), f"{header}.{payload}".encode("ascii"), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    return f"{header}.{payload}.{signature}"


def verify_scrypt_password(password: str, encoded: str) -> bool:
    """Verify the documented scrypt$N$r$p$salt_b64$digest_b64 configuration format."""
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = _decode(salt_text)
        expected = _decode(digest_text)
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(derived, expected)
    except (TypeError, ValueError, OverflowError, AttributeError):
        return False


def authenticate_jwt(token: str, *, secret: str, issuer: str = "",
                     audience: str = "", now: int | None = None,
                     expected_token_type: str | None = None) -> Requester:
    """Validate an externally issued HS256 JWT and return verified identity."""
    if not secret:
        raise JWTValidationError("JWT authentication is not configured")
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTValidationError("Malformed access token")
    header_raw, claims_raw, signature = parts
    try:
        header = json.loads(_decode(header_raw))
        claims: dict[str, Any] = json.loads(_decode(claims_raw))
    except (TypeError, json.JSONDecodeError) as exc:
        raise JWTValidationError("Malformed access token") from exc
    if header.get("alg") != "HS256":
        raise JWTValidationError("Unsupported token algorithm")
    expected = hmac.new(secret.encode("utf-8"), f"{header_raw}.{claims_raw}".encode("ascii"), hashlib.sha256).digest()
    try:
        supplied = _decode(signature)
    except JWTValidationError:
        raise
    if not hmac.compare_digest(expected, supplied):
        raise JWTValidationError("Invalid token signature")
    timestamp = int(time.time() if now is None else now)
    if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
        raise JWTValidationError("Token subject is required")
    if not isinstance(claims.get("exp"), (int, float)) or int(claims["exp"]) <= timestamp:
        raise JWTValidationError("Access token has expired")
    if issuer and claims.get("iss") != issuer:
        raise JWTValidationError("Invalid token issuer")
    if audience:
        audiences = claims.get("aud")
        if audience not in (audiences if isinstance(audiences, list) else [audiences]):
            raise JWTValidationError("Invalid token audience")
    if expected_token_type and claims.get("typ") != expected_token_type:
        raise JWTValidationError("Invalid token type")
    permissions = claims.get("permissions", [])
    if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions):
        raise JWTValidationError("Invalid token permissions")
    role = claims.get("role", "")
    if not isinstance(role, str):
        raise JWTValidationError("Invalid token role")
    return Requester(claims["sub"], True, frozenset(permissions), role=role, authentication="jwt")
