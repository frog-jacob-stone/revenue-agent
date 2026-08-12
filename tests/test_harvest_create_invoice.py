"""The one Harvest write, and its retry rule (PRD §8 steps 2 and 5).

The property under test is not "does it work" — it is **how many times it POSTs**.
Harvest has no idempotency keys, so a second POST is a second invoice. Every test
here asserts the attempt count, because that is the number that turns into
duplicate money.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.config import Settings
from app.integrations import harvest
from app.integrations.harvest_limiter import HarvestRateLimiter

_PAYLOAD = {"client_id": 1, "line_items": [{"kind": "Service", "unit_price": 100.0}]}
_CREATED = {"id": 9001, "number": "INV-0042", "amount": 100.0, "state": "draft"}


@pytest.fixture
def cfg() -> Settings:
    """Only the fields `_post` actually reads, so a rename breaks a test rather
    than silently doing nothing — `extra="ignore"` on Settings means a misspelled
    field here is dropped without complaint."""
    return Settings(
        harvest_token="test-token",
        harvest_account_id="12345",
        harvest_user_agent_contact="tests@frogslayer.com",
    )


@pytest.fixture
def limiter() -> HarvestRateLimiter:
    """A limiter that never actually sleeps, so a 429 backoff costs no wall time."""

    async def _no_sleep(_seconds: float) -> None:
        return None

    return HarvestRateLimiter(clock=lambda: 0.0, sleeper=_no_sleep)


class _Recorder:
    """Counts POSTs and replays a scripted sequence of responses."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json

        self.calls.append((str(request.url), json.loads(request.content)))
        nxt = self._responses.pop(0) if self._responses else self._responses_exhausted()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def _responses_exhausted(self) -> httpx.Response:
        raise AssertionError(
            f"POST attempted {len(self.calls)} times; the script only allowed "
            f"{len(self.calls) - 1}. An unexpected retry is a duplicate invoice."
        )

    @property
    def attempts(self) -> int:
        return len(self.calls)


@pytest.fixture
def post_transport(monkeypatch):
    """Install a scripted transport and hand back the recorder."""

    def _install(*responses: httpx.Response | Exception) -> _Recorder:
        rec = _Recorder(*responses)
        real_client = httpx.AsyncClient

        def _factory(*_a: Any, **kw: Any) -> httpx.AsyncClient:
            return real_client(transport=httpx.MockTransport(rec.handler), **kw)

        monkeypatch.setattr(harvest.httpx, "AsyncClient", _factory)
        return rec

    return _install


def _resp(status: int, body: Any = None, headers: dict[str, str] | None = None):
    return httpx.Response(status, json=body if body is not None else {}, headers=headers)


async def test_201_returns_the_created_invoice(cfg, limiter, post_transport):
    rec = post_transport(_resp(201, _CREATED))

    invoice = await harvest._post(cfg, "/invoices", _PAYLOAD, limiter=limiter)

    assert invoice == _CREATED
    assert rec.attempts == 1
    url, body = rec.calls[0]
    assert url == "https://api.harvestapp.com/v2/invoices"
    assert body == _PAYLOAD


async def test_422_raises_validation_error_and_does_not_retry(cfg, limiter, post_transport):
    """A 422 means Harvest rejected the payload — nothing was created, and
    re-sending the same rejected body would only be rejected again."""
    rec = post_transport(_resp(422, {"message": "Project does not belong to client"}))

    with pytest.raises(harvest.HarvestValidationError) as exc:
        await harvest._post(cfg, "/invoices", _PAYLOAD, limiter=limiter)

    assert rec.attempts == 1
    assert "does not belong" in str(exc.value.body)


async def test_429_then_201_retries_once(cfg, limiter, post_transport):
    """The one safe retry: a 429 never reached invoice creation."""
    rec = post_transport(
        _resp(429, {"message": "slow down"}, {"Retry-After": "0"}),
        _resp(201, _CREATED),
    )

    invoice = await harvest._post(cfg, "/invoices", _PAYLOAD, limiter=limiter)

    assert invoice == _CREATED
    assert rec.attempts == 2


async def test_429_past_the_cap_raises(cfg, limiter, post_transport):
    """Retries are bounded. Four 429s (initial + _MAX_429_RETRIES) then give up."""
    rec = post_transport(
        *[_resp(429, {"message": "slow down"}, {"Retry-After": "0"})] * 4
    )

    with pytest.raises(harvest.HarvestRateLimited):
        await harvest._post(cfg, "/invoices", _PAYLOAD, limiter=limiter)

    assert rec.attempts == harvest._MAX_429_RETRIES + 1


async def test_500_raises_without_retrying(cfg, limiter, post_transport):
    """A 5xx may or may not have created the invoice. Retrying would risk a
    duplicate, so it propagates on the first attempt."""
    rec = post_transport(_resp(500, {"message": "boom"}))

    with pytest.raises(harvest.HarvestServerError):
        await harvest._post(cfg, "/invoices", _PAYLOAD, limiter=limiter)

    assert rec.attempts == 1


async def test_timeout_propagates_after_exactly_one_attempt(cfg, limiter, post_transport):
    """The most important assertion in this file.

    A timeout is the ambiguous case: the invoice may exist in Harvest. Exactly
    one POST must have been made, and the exception must reach the caller so it
    can leave its ledger row in_flight rather than guessing.
    """
    rec = post_transport(httpx.TimeoutException("timed out"))

    with pytest.raises(httpx.TimeoutException):
        await harvest._post(cfg, "/invoices", _PAYLOAD, limiter=limiter)

    assert rec.attempts == 1


async def test_connection_error_propagates_after_exactly_one_attempt(
    cfg, limiter, post_transport
):
    rec = post_transport(httpx.ConnectError("no route"))

    with pytest.raises(httpx.ConnectError):
        await harvest._post(cfg, "/invoices", _PAYLOAD, limiter=limiter)

    assert rec.attempts == 1


async def test_create_invoice_posts_to_invoices(cfg, post_transport):
    """`create_invoice` is a thin wrapper; this pins the path and the passthrough."""
    rec = post_transport(_resp(201, _CREATED))

    invoice = await harvest.create_invoice(cfg, _PAYLOAD)

    assert invoice == _CREATED
    assert rec.calls[0][0].endswith("/v2/invoices")


async def test_create_invoice_sends_no_retainer_id(cfg, post_transport):
    """C-constraint: Harvest's retainer object must never be touched. The
    guardrail test scans source for `retainer_id`; this checks the wire."""
    rec = post_transport(_resp(201, _CREATED))

    await harvest.create_invoice(cfg, _PAYLOAD)

    assert "retainer_id" not in rec.calls[0][1]
