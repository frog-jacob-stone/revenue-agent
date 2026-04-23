import json
import os
import uuid
from pathlib import Path

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
import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

_MIGRATION_SQL = (
    Path(__file__).parent.parent / "supabase" / "migrations" / "0001_initial_schema.sql"
).read_text()


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

    # CREATE DATABASE must run outside a transaction on a different DB.
    admin = await asyncpg.connect(admin_url)
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()

    # Apply migration to the test DB (simple-query protocol handles BEGIN/COMMIT).
    migrate = await asyncpg.connect(_TEST_DB_URL)
    try:
        await _init_conn(migrate)
        await migrate.execute(_MIGRATION_SQL)
    finally:
        await migrate.close()

    pool = await asyncpg.create_pool(_TEST_DB_URL, min_size=2, max_size=5, init=_init_conn)

    # Inject into the app so get_pool() returns our pool, never the real one.
    import app.db as _db
    _db._pool = pool

    yield pool

    _db._pool = None
    await pool.close()


@pytest.fixture(scope="session")
async def test_agent_id(_test_pool: asyncpg.Pool) -> uuid.UUID:
    """
    Inserts a test agent once per session, committed directly to postgres_test.
    Committed before any per-test rollback transaction starts, so it is visible
    to all tests.
    """
    slug = f"test_{uuid.uuid4().hex[:8]}"
    return await _test_pool.fetchval(
        "INSERT INTO agents (slug, name, requires_approval, approval_scope, config) "
        "VALUES ($1, $2, false, '{}'::text[], '{}'::jsonb) RETURNING id",
        slug,
        "Test Agent",
    )


@pytest.fixture(scope="session")
async def client(_test_pool: asyncpg.Pool) -> AsyncClient:
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
