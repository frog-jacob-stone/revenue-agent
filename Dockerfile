# syntax=docker/dockerfile:1.7

# ---- builder: resolve runtime dependencies only ----
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    # Build the venv at the path it will occupy at runtime. Console scripts bake
    # an absolute shebang, so a venv built at /build/.venv and copied elsewhere
    # produces `exec /opt/venv/bin/uvicorn: no such file or directory` -- the
    # interpreter in the shebang is what is missing, not the script.
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# Only the lock inputs, so this layer survives every change to app/.
COPY pyproject.toml uv.lock ./

# --frozen fails loudly if uv.lock is stale rather than silently resolving
# something different from what CI tested. --no-dev keeps pytest and ruff out of
# the runtime image; the previous `pip install ".[test]"` shipped the whole test
# stack to production. --no-install-project because app/ is copied directly
# below: it is one package with no build step, so installing a wheel would only
# add a rebuild on every code change.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---- runtime ----
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}"

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
# WORKDIR first, so this lands at /app/app and `import app.main` resolves from
# the working directory.
COPY app/ app/

# Files stay root-owned and the process is not: the app cannot rewrite its own
# code. Nothing here writes to disk, and the venv is already byte-compiled, so an
# unwritable tree costs nothing at startup.
USER 10001

EXPOSE 8000

# No HEALTHCHECK. Container Apps ignores it and runs its own probes against
# /healthz and /readyz, configured on the container app itself (see DEPLOY.md);
# a second definition here would be a silently diverging source of truth.
#
# Every flag below is load-bearing:
#   --workers 1              This app has module-level singletons -- the Harvest
#                            token bucket, the in-memory turn registry, the
#                            asyncpg pool, the JWKS cache. A second worker breaks
#                            it exactly as badly as a second replica. Explicit so
#                            that is a decision rather than a default. Never add
#                            gunicorn.
#   --timeout-keep-alive 75  Above the ingress proxy's idle window, so the origin
#                            is not the side that closes a pooled connection --
#                            which shows up as intermittent 502s.
#   --timeout-graceful-      Strictly below the platform's 60s termination grace,
#     shutdown 50            so uvicorn drains on its own terms instead of being
#                            SIGKILLed mid-response.
#   --forwarded-allow-ips *  Makes uvicorn trust X-Forwarded-Proto/For. Safe only
#                            because the ingress is the sole network peer that
#                            can reach this container. Revisit if the app ever
#                            makes decisions on client IP.
#   --no-server-header       Drops the uvicorn version advertisement.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--timeout-keep-alive", "75", \
     "--timeout-graceful-shutdown", "50", \
     "--no-server-header", \
     "--log-level", "info"]
