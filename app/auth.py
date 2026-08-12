from uuid import UUID

import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWKClient
from pydantic import BaseModel

from app.config import settings


class AuthUser(BaseModel):
    id: UUID
    email: str | None
    role: str


_jwk_client: PyJWKClient | None = None


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        if not settings.supabase_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Auth not configured: SUPABASE_URL missing",
            )
        jwks_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        # `timeout` is explicit: PyJWT defaults to 30s, and this fetch blocks the
        # request that triggered it. Cached for an hour, so the cost is paid once
        # an hour at most — but 30s of a held request is not a cost worth paying.
        _jwk_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5)
    return _jwk_client


_ASYMMETRIC_ALGS = ("ES256", "RS256")


def verify_supabase_jwt(token: str) -> dict:
    """Verify a Supabase-issued JWT against the project's JWKS.

    Asymmetric only (`ES256`/`RS256`, verified via
    `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`). Any other `alg` — including
    `HS256` — is rejected with a 401.

    There used to be an HS256 branch using a shared `SUPABASE_JWT_SECRET`, kept
    for older Supabase projects. This project issues asymmetric tokens, so the
    branch was unreachable in production and the secret existed only to let the
    auth tests mint their own tokens. Dropping it removes a second, weaker way to
    authenticate: a leaked shared secret would have been enough to forge a token
    for any user, which is not true of the JWKS path.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    alg = header.get("alg")
    try:
        if alg in _ASYMMETRIC_ALGS:
            signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=[alg],
                audience="authenticated",
                options={"require": ["exp", "sub"]},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unsupported token algorithm: {alg}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization") or request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return None


def _user_from_claims(claims: dict) -> AuthUser:
    return AuthUser(
        id=UUID(claims["sub"]),
        email=claims.get("email"),
        role=claims.get("role", "authenticated"),
    )


async def get_current_user(request: Request) -> AuthUser:
    token = _extract_bearer(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = _user_from_claims(verify_supabase_jwt(token))
    request.state.user = user
    return user


# There was a `get_current_user_from_query_or_header` here: an SSE-friendly
# variant that also accepted `?access_token=...` for EventSource clients. No
# router ever used it, and the SSE path does not need it — the UI streams with
# `fetch` and an Authorization header (ui/src/api.ts), not EventSource.
#
# Removed rather than kept "in case": a token in a query string is recorded by
# every proxy access log it passes, and an unused auth path is one someone wires
# up in a hurry later. If EventSource is ever genuinely needed, the answer is a
# short-lived single-use ticket, not the session JWT in a URL.
# `tests/test_auth.py` pins that a query-param token alone still gets a 401.
