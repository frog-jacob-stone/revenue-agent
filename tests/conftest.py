import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load app/.env into os.environ so test fixtures can read TEST_USER_* etc.
# `override=False` so pytest-env values in pyproject.toml still win.
_APP_ENV = Path(__file__).parent.parent / "app" / ".env"
if _APP_ENV.exists():
    load_dotenv(_APP_ENV, override=False)

# Tests run on the host, not in Docker, so host.docker.internal won't resolve.
# Rewrite SUPABASE_URL the same way pytest-env hard-codes TEST_DATABASE_URL.
if "host.docker.internal" in os.environ.get("SUPABASE_URL", ""):
    os.environ["SUPABASE_URL"] = os.environ["SUPABASE_URL"].replace(
        "host.docker.internal", "127.0.0.1"
    )

# ── Fail loudly before any app import ────────────────────────────────────────
_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
if not _TEST_DB_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set.\n"
        "Add it to .env:\n"
        "  TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres_test"
    )

# Override DATABASE_URL so Settings._guard_test_db passes when ENV=test.
# Must happen before any `from app.*` import causes Settings() to be evaluated.
os.environ["DATABASE_URL"] = _TEST_DB_URL
# belt-and-suspenders: pytest-env also sets ENV=test via pyproject.toml
os.environ.setdefault("ENV", "test")

# ── Now safe to import everything else ───────────────────────────────────────
# noqa: E402 on each — the position is load-bearing, not accidental. These must
# follow the os.environ writes above, because importing anything under `app.`
# evaluates Settings(), and Settings() reads DATABASE_URL/ENV exactly once.
import asyncpg  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

_MIGRATIONS_DIR = Path(__file__).parent.parent / "supabase" / "migrations"
_MIGRATION_SQLS = [
    f.read_text() for f in sorted(_MIGRATIONS_DIR.glob("*.sql"))
]


# ── asyncpg JSONB codec ───────────────────────────────────────────────────────

async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


# ── Fake pool that pins every acquire() to one connection ────────────────────

class _SingleConnPool:
    """
    Duck-types asyncpg.Pool for a single connection.
    Used in _rollback so the entire test runs on the same connection
    (letting us roll back the outer transaction at the end).
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    def acquire(self) -> "_SingleConnPool._Ctx":
        return self._Ctx(self._conn)

    async def fetch(self, q, *a, **kw):
        return await self._conn.fetch(q, *a, **kw)

    async def fetchrow(self, q, *a, **kw):
        return await self._conn.fetchrow(q, *a, **kw)

    async def fetchval(self, q, *a, **kw):
        return await self._conn.fetchval(q, *a, **kw)

    async def execute(self, q, *a, **kw):
        return await self._conn.execute(q, *a, **kw)

    class _Ctx:
        def __init__(self, conn: asyncpg.Connection) -> None:
            self._conn = conn

        async def __aenter__(self) -> asyncpg.Connection:
            return self._conn

        async def __aexit__(self, *_) -> None:
            pass  # release is handled by _rollback, not by the app


# ── Session-scoped fixtures ───────────────────────────────────────────────────

@pytest.fixture(scope="session")
async def _test_pool() -> asyncpg.Pool:
    """
    1. Creates postgres_test DB if absent.
    2. Applies the migration (idempotent – all statements use IF NOT EXISTS / CREATE OR REPLACE).
    3. Returns an asyncpg pool and injects it into app.db so get_pool() never
       touches the real database.
    """
    db_name = _TEST_DB_URL.rstrip("/").rsplit("/", 1)[-1]
    admin_url = _TEST_DB_URL.rsplit("/", 1)[0] + "/postgres"

    # Drop and recreate the test DB each session for a clean slate.
    # Necessary because CREATE POLICY is not idempotent in migration 0001.
    admin = await asyncpg.connect(admin_url)
    try:
        # Migration 0001 generates RLS policies `to service_role`, and Postgres
        # requires the role to exist. A Supabase cluster pre-provisions these; a
        # stock postgres image does not, which is what CI runs. Roles are
        # cluster-scoped, so creating them here survives the DROP DATABASE below.
        # `anon` and `authenticated` are unreferenced today and created anyway —
        # they cost nothing and pre-empt the next RLS migration.
        for role in ("anon", "authenticated", "service_role"):
            await admin.execute(
                f"do $$ begin create role {role} nologin noinherit; "
                f"exception when duplicate_object then null; end $$;"
            )
        await admin.execute(
            f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'
        )
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()

    # Apply migration to the test DB (simple-query protocol handles BEGIN/COMMIT).
    migrate = await asyncpg.connect(_TEST_DB_URL)
    try:
        await _init_conn(migrate)
        for sql in _MIGRATION_SQLS:
            await migrate.execute(sql)
    finally:
        await migrate.close()

    pool = await asyncpg.create_pool(_TEST_DB_URL, min_size=2, max_size=5, init=_init_conn)

    # Inject into the app so get_pool() returns our pool, never the real one.
    import app.db as _db
    _db._pool = pool

    yield pool

    _db._pool = None
    await pool.close()


_TEST_AGENT_SLUG = "test-agent"


@pytest.fixture(scope="session")
async def test_agent_id(_test_pool: asyncpg.Pool) -> uuid.UUID:
    """
    Inserts a test agent once per session, committed directly to postgres_test.
    Committed before any per-test rollback transaction starts, so it is visible
    to all tests.
    """
    return await _test_pool.fetchval(
        "INSERT INTO agents (slug) VALUES ($1) RETURNING id",
        _TEST_AGENT_SLUG,
    )


@pytest.fixture(scope="session")
async def test_agent_slug(test_agent_id: uuid.UUID) -> str:
    # Requesting test_agent_id ensures the agent row is inserted before this fixture returns.
    _ = test_agent_id
    return _TEST_AGENT_SLUG


@pytest.fixture(scope="session")
def _test_user_credentials() -> tuple[str, str]:
    """Test user email/password from env. The user is created on demand below."""
    email = os.environ.get("TEST_USER_EMAIL")
    password = os.environ.get("TEST_USER_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "TEST_USER_EMAIL and TEST_USER_PASSWORD must be set in app/.env."
        )
    return email, password


@pytest.fixture(scope="session")
async def test_access_token(_test_user_credentials: tuple[str, str]) -> str:
    """A real access token. Session-scoped, so auth is hit once per run.

    Two modes, chosen by `TEST_AUTH_MODE`:

    - `supabase` (default) — a genuine password grant against the local Supabase
      Auth server. Highest fidelity, and what a developer gets by default.
    - `local-key` — sign a token with an ephemeral key and point `app.auth` at a
      stub JWKS client. Used by CI, which has Postgres but no Supabase stack. See
      tests/_local_jwt.py for exactly what this does and does not still verify.
    """
    if os.environ.get("TEST_AUTH_MODE", "supabase") == "local-key":
        from tests._local_jwt import install

        return install()

    import httpx

    from app.config import settings

    # Read from the environment rather than from Settings. The publishable key
    # is needed only to talk to Supabase Auth from this fixture — no application
    # code path reads it — so it is not a Settings field, and having it there
    # meant every production deploy carried a variable the app never used.
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not settings.supabase_url or not publishable_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be set in app/.env."
        )
    email, password = _test_user_credentials
    base = settings.supabase_url.rstrip("/")
    headers = {"apikey": publishable_key, "Content-Type": "application/json"}
    creds = {"email": email, "password": password}

    async with httpx.AsyncClient(timeout=10.0) as http:

        async def sign_in() -> httpx.Response:
            return await http.post(
                f"{base}/auth/v1/token",
                params={"grant_type": "password"},
                headers=headers,
                json=creds,
            )

        res = await sign_in()

        # Create the user rather than telling a human to. This used to be a
        # manual step in Supabase Studio, which meant a fresh clone could not run
        # the suite and CI could not run it at all. The local stack has
        # enable_signup with confirmations off (supabase/config.toml), so the
        # public endpoint yields a usable, pre-confirmed user with nothing but
        # the anon key — no service-role key, and no hand-written rows in
        # GoTrue's private schema, which changes shape between releases.
        #
        # Guarded on ENV=test, and on a hosted project signups are disabled, so
        # this cannot create users anywhere but a local stack.
        if res.status_code == 400 and settings.env == "test":
            await http.post(f"{base}/auth/v1/signup", headers=headers, json=creds)
            res = await sign_in()

    if res.status_code != 200:
        raise RuntimeError(
            f"Failed to sign in test user (status={res.status_code}): {res.text}\n"
            f"Is `supabase start` running, and do TEST_USER_EMAIL / "
            f"TEST_USER_PASSWORD in app/.env match an existing user? "
            f"(Set TEST_AUTH_MODE=local-key to run without Supabase Auth.)"
        )
    return res.json()["access_token"]


@pytest.fixture(scope="session")
async def client(_test_pool: asyncpg.Pool, test_access_token: str) -> AsyncClient:
    """Authenticated client — attaches a real bearer token from a Supabase
    sign-in so requests hit the real JWT verification path."""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {test_access_token}"},
    ) as c:
        yield c


@pytest.fixture(scope="session")
async def unauthed_client(_test_pool: asyncpg.Pool) -> AsyncClient:
    """Client with no auth — for tests that exercise the 401 path directly."""
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Per-test rollback (autouse) ───────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def _rollback(_test_pool: asyncpg.Pool) -> None:
    """
    Wraps every test in an outer transaction that is always rolled back.

    Strategy:
      1. Acquire a connection from _test_pool.
      2. Begin a transaction manually.
      3. Replace app.db._pool with _SingleConnPool(conn) so every DB call
         the app makes during the test goes through this one connection —
         nested conn.transaction() calls become SAVEPOINTs automatically.
      4. After the test (pass or fail), roll back the outer transaction.
         app.db._pool is restored to _test_pool for the next test.

    Net result: zero rows persist after any test completes.
    """
    import app.db as _db

    conn = await _test_pool.acquire()
    tr = conn.transaction()
    await tr.start()

    _db._pool = _SingleConnPool(conn)
    yield
    # Restore before rollback so teardown of other fixtures sees the real pool.
    _db._pool = _test_pool
    await tr.rollback()
    await _test_pool.release(conn)
