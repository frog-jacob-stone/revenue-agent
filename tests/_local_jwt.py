"""An ephemeral ES256 signer, so CI can run without a live Supabase Auth server.

The suite's default is still a real password grant against `supabase start` —
that is the highest-fidelity check and it stays the local default. But CI does
not need it, because **the application never talks to GoTrue**. `app/auth.py`
verifies a JWT against a JWKS document; GoTrue's only contribution is *minting*
the token. Standing up a twelve-container stack on every pull request to have a
third party generate a JWT is minutes of wall clock and a recurring source of
flakes, bought for nothing.

What this replaces is narrow and worth stating precisely: only the JWKS *fetch*.
Everything `verify_supabase_jwt` actually asserts still runs against the token
produced here — the algorithm allowlist, the `authenticated` audience, and the
`require=["exp", "sub"]` options. A token that would fail in production fails
here.

What it does NOT cover is that Supabase issues tokens in the shape we expect. A
nightly workflow runs the full `supabase start` path for that, and
`test_valid_token_passes` exercises it locally.

`cryptography` is already present via the `pyjwt[crypto]` runtime dependency, so
this adds no package.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

_KEY = ec.generate_private_key(ec.SECP256R1())


class _StubSigningKey:
    def __init__(self, key) -> None:
        self.key = key


class StubJWKClient:
    """Stands in for `PyJWKClient`, returning the local public key for any token.

    Deliberately does not inspect the `kid`: the point is to remove the network
    fetch, not to reimplement key selection.
    """

    def get_signing_key_from_jwt(self, token: str) -> _StubSigningKey:
        return _StubSigningKey(_KEY.public_key())


def install() -> str:
    """Patch `app.auth`'s JWKS client and return a token that verifies against it."""
    import app.auth as _auth

    _auth._jwk_client = StubJWKClient()

    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(uuid4()),
            "aud": "authenticated",
            "role": "authenticated",
            "email": "ci@example.test",
            "iat": now,
            "exp": now + timedelta(hours=2),
        },
        _KEY,
        algorithm="ES256",
    )
