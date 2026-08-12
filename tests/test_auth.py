import time
from uuid import uuid4

import jwt
import pytest
from httpx import AsyncClient

from app import auth as auth_mod
from app.config import settings


_TEST_SECRET = "test-jwt-secret-at-least-32-characters-long-xxx"


@pytest.fixture(autouse=True)
def _set_jwt_secret(monkeypatch):
    """Force the HS256 fallback path in verify_supabase_jwt for these tests."""
    monkeypatch.setattr(settings, "supabase_jwt_secret", _TEST_SECRET)
    # Also reset the cached JWKS client so it isn't reused across tests.
    monkeypatch.setattr(auth_mod, "_jwk_client", None)


def _mint(claims: dict | None = None, secret: str = _TEST_SECRET, exp_offset: int = 3600) -> str:
    now = int(time.time())
    payload = {
        "sub": str(uuid4()),
        "email": "jacob@frogslayer.com",
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + exp_offset,
        **(claims or {}),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


async def test_healthz_is_public(unauthed_client: AsyncClient):
    res = await unauthed_client.get("/healthz")
    assert res.status_code == 200


async def test_missing_token_returns_401(unauthed_client: AsyncClient):
    res = await unauthed_client.get("/agents")
    assert res.status_code == 401


async def test_malformed_token_returns_401(unauthed_client: AsyncClient):
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert res.status_code == 401


async def test_expired_token_returns_401(unauthed_client: AsyncClient):
    token = _mint(exp_offset=-60)
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 401


async def test_wrong_signature_returns_401(unauthed_client: AsyncClient):
    token = _mint(secret="some-other-secret-at-least-32-characters-long-x")
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 401


async def test_valid_token_passes(
    unauthed_client: AsyncClient, test_access_token: str
):
    """Real end-to-end: a token issued by the local Supabase Auth server
    (signed with the project's ES256 JWKS key) verifies cleanly."""
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": f"Bearer {test_access_token}"}
    )
    assert res.status_code == 200


async def test_wrong_scheme_returns_401(unauthed_client: AsyncClient):
    token = _mint()
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": f"Basic {token}"}
    )
    assert res.status_code == 401
