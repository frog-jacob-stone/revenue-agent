"""Every registered route method must be one the browser can preflight.

This is a structural guardrail, in the same spirit as
`test_harvest_write_guardrail.py`: the property we want is "no route exists that
the UI cannot call", not "the routes we happened to exercise worked".

It exists because that gap is invisible to every other test in this suite.
httpx's `ASGITransport` calls the app directly and never issues a CORS preflight,
so a route whose method is missing from `allow_methods` passes its own router
test with a 200 and then fails in the browser with an `OPTIONS … 400` and a bare
"Failed to fetch" — no traceback, nothing in the API log connecting it to the
route. It happened once, adding the app's first `PUT`; one afternoon of
confusion is enough to buy a test.
"""
from __future__ import annotations

from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Route

from app.main import app

# Methods Starlette attaches to every route without us asking. They are never
# called cross-origin in their own right, so they are not the subject here.
_IMPLICIT = {"HEAD", "OPTIONS"}


def _allowed_methods() -> set[str]:
    """What the CORS middleware will let a browser preflight."""
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            return set(mw.kwargs["allow_methods"])
    raise AssertionError(
        "CORSMiddleware is not installed on the app — every browser request "
        "from the UI origin would be blocked"
    )


def test_no_route_uses_a_method_cors_would_reject():
    allowed = _allowed_methods()
    if "*" in allowed:
        return  # a wildcard allows everything; nothing to check

    offenders: list[str] = []
    for route in app.routes:
        if not isinstance(route, Route) or not route.methods:
            continue
        for method in route.methods - _IMPLICIT:
            if method not in allowed:
                offenders.append(f"{method} {route.path}")

    assert not offenders, (
        "These routes use an HTTP method the CORS middleware does not allow, so "
        "the browser's preflight gets a 400 and the UI sees only 'Failed to "
        "fetch'. Either use a method already in `allow_methods` (preferred — "
        "match the surrounding router) or add the method in app/main.py:\n  "
        + "\n  ".join(sorted(offenders))
    )
