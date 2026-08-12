"""Auth: JWKS verification, and everything that must return 401.

These tests used to mint HS256 tokens against `settings.supabase_jwt_secret`,
because that was the only algorithm a test could sign for. That branch is gone —
`verify_supabase_jwt` now accepts ES256/RS256 only — so the tests generate a real
P-256 keypair and stub the JWKS client with its public key.

That matters for more than parity. Under the old setup, "expired" and "wrong
signature" were only ever proven on the HS256 branch, which production never
took. They are now proven on the branch that actually runs.
"""
import os
import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient

from app import auth as auth_mod

# Two keypairs: one the stubbed JWKS endpoint vouches for, one it does not.
_KEY = ec.generate_private_key(ec.SECP256R1())
_WRONG_KEY = ec.generate_private_key(ec.SECP256R1())


class _StubSigningKey:
    def __init__(self, key):
        self.key = key


class _StubJWKClient:
    """Stands in for PyJWKClient, vouching for `_KEY` and nothing else."""

    def get_signing_key_from_jwt(self, token):  # noqa: ARG002 - signature parity
        return _StubSigningKey(_KEY.public_key())


# Captured before anything patches it, so `real_jwks` can put it back.
_REAL_GET_JWK_CLIENT = auth_mod._get_jwk_client


@pytest.fixture(autouse=True)
def _stub_jwks(monkeypatch):
    monkeypatch.setattr(auth_mod, "_get_jwk_client", lambda: _StubJWKClient())
    # Drop any client cached by an earlier test so it can't leak across cases.
    monkeypatch.setattr(auth_mod, "_jwk_client", None)


@pytest.fixture
def real_jwks(_stub_jwks, monkeypatch):
    """Undo the autouse stub for the one test that uses a real Supabase token.

    Depends on `_stub_jwks` to guarantee it runs *after* it — otherwise fixture
    ordering decides whether the stub or the real client wins, and the test would
    pass or fail depending on that.
    """
    monkeypatch.setattr(auth_mod, "_get_jwk_client", _REAL_GET_JWK_CLIENT)
    monkeypatch.setattr(auth_mod, "_jwk_client", None)


def _mint(
    claims: dict | None = None,
    *,
    key=None,
    exp_offset: int = 3600,
    alg: str = "ES256",
) -> str:
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
    signing_key = key if key is not None else _KEY
    return jwt.encode(payload, signing_key, algorithm=alg)


async def test_healthz_is_public(unauthed_client: AsyncClient):
    res = await unauthed_client.get("/healthz")
    assert res.status_code == 200


async def test_readyz_is_public(unauthed_client: AsyncClient):
    """The container platform's probes carry no credentials.

    Both probe routes have to stay outside the auth dependency or every check
    fails 401, the revision never goes healthy, and the deploy hangs on what
    looks like a startup problem.
    """
    res = await unauthed_client.get("/readyz")
    assert res.status_code == 200
    assert res.json() == {"status": "ready"}


async def test_readyz_reports_503_when_the_database_is_unreachable(
    unauthed_client: AsyncClient, monkeypatch
):
    """Readiness has to actually check something, or it is just /healthz twice."""

    async def _broken_pool():
        raise ConnectionError("simulated: Postgres unreachable")

    monkeypatch.setattr("app.main.get_pool", _broken_pool)
    res = await unauthed_client.get("/readyz")
    assert res.status_code == 503


async def test_readyz_body_carries_no_configuration(unauthed_client: AsyncClient):
    """It is unauthenticated, so the body must not describe the deployment."""
    body = (await unauthed_client.get("/readyz")).text
    for leak in ("postgres", "supabase", "version", "env"):
        assert leak not in body.lower()


async def test_missing_token_returns_401(unauthed_client: AsyncClient):
    res = await unauthed_client.get("/agents")
    assert res.status_code == 401


async def test_malformed_token_returns_401(unauthed_client: AsyncClient):
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert res.status_code == 401


async def test_expired_token_returns_401(unauthed_client: AsyncClient):
    """Correctly signed, past `exp` — rejected on expiry, not on algorithm."""
    token = _mint(exp_offset=-60)
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 401
    assert "expired" in res.json()["detail"].lower()


async def test_wrong_signature_returns_401(unauthed_client: AsyncClient):
    """Right algorithm, wrong key — the signature check has to be what fails."""
    token = _mint(key=_WRONG_KEY)
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 401
    assert "expired" not in res.json()["detail"].lower()


async def test_hs256_token_is_rejected(unauthed_client: AsyncClient):
    """The dropped fallback stays dropped.

    A shared-secret token is the weaker way in: leaking that one secret would let
    anyone forge a token for any user. Asserting the rejection means re-adding the
    branch cannot pass unnoticed.
    """
    token = _mint(key="a-shared-secret-at-least-32-characters-long", alg="HS256")
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 401
    assert "algorithm" in res.json()["detail"].lower()


async def test_wrong_scheme_returns_401(unauthed_client: AsyncClient):
    token = _mint()
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": f"Basic {token}"}
    )
    assert res.status_code == 401


async def test_query_param_token_is_rejected(unauthed_client: AsyncClient):
    """The dropped query-param path stays dropped.

    `get_current_user_from_query_or_header` accepted `?access_token=...` for
    EventSource clients. Nothing used it, and a JWT in a URL is written to every
    proxy access log between the browser and the app. Asserting the rejection
    means re-adding it cannot pass unnoticed — including via a valid token, which
    is what makes this different from the missing-token case above.
    """
    token = _mint()
    res = await unauthed_client.get(f"/agents?access_token={token}")
    assert res.status_code == 401


@pytest.mark.skipif(
    os.environ.get("TEST_AUTH_MODE") == "local-key",
    reason=(
        "Requires a live Supabase Auth server to mint the token and publish the "
        "JWKS. This is the one test local-key mode cannot cover, which is exactly "
        "why the nightly workflow runs the full `supabase start` path."
    ),
)
async def test_valid_token_passes(
    unauthed_client: AsyncClient, test_access_token: str, real_jwks
):
    """Real end-to-end: no stub anywhere in the signing path.

    The token comes from the local Supabase Auth server, signed by the project's
    actual ES256 key, and `real_jwks` restores the genuine JWKS client so the
    signature is checked against the published key rather than a test one.
    """
    res = await unauthed_client.get(
        "/agents", headers={"Authorization": f"Bearer {test_access_token}"}
    )
    assert res.status_code == 200
