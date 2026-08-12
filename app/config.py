from pathlib import Path
from typing import Literal

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationError,
    computed_field,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# The values Settings falls back to when nothing sets them. Named so the
# production guard can tell "still the local-dev default" apart from
# "deliberately set to this", which is the difference between a forgotten
# variable and a choice.
_DEV_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
_DEV_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "host.docker.internal", "::1")

# app/.env — anchored to this file, not to the working directory.
#
# This was `env_file=".env"`, which pydantic-settings resolves relative to the
# process cwd. There is no `.env` at the repo root, so launching from there (as
# README's non-Docker instructions say to) silently loaded nothing: every
# credential fell back to its empty-string default and the first Harvest call
# failed on a blank token. Anchoring makes the launch directory irrelevant.
#
# Under `docker compose` this file is still not what supplies the values —
# compose reads ./app/.env itself and injects the pairs as real environment
# variables, and `.dockerignore` keeps any .env out of the image. Environment
# variables outrank an env file in pydantic-settings, so the two agree; this only
# changes the case where nothing else populated the environment.
_ENV_FILE = Path(__file__).parent / ".env"


def _redact_dsn(dsn: str) -> str:
    """A connection string with the credentials stripped, for error messages.

    `postgresql://user:pw@host:5432/db` -> `postgresql://***@host:5432/db`. Keeps
    the part you need to diagnose a wrong-database error and drops the part you
    must never log.
    """
    if "@" not in dsn:
        return dsn
    scheme, _, rest = dsn.partition("://")
    _creds, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}" if host else dsn


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # Credentials are `SecretStr` so they render as `**********` in a repr.
    # Pydantic prints every field value by default, so an unrelated validation
    # error — or any failing test that renders a Settings object — used to dump
    # live tokens into terminal output and CI logs. Read one with
    # `.get_secret_value()`; `test_config_redaction.py` guards the redaction.

    # Infrastructure
    log_level: str = "INFO"
    # A `Literal`, not a `str`, so `ENV=prod` fails at startup instead of quietly
    # disabling `guard_production_config` — which keys off the exact value
    # "production" and would otherwise wave a misconfigured deploy straight
    # through. Pydantic's literal-mismatch message lists the permitted values,
    # not the supplied one, so it stays safe to print; `_load_settings` sanitizes
    # it regardless.
    env: Literal["development", "test", "production"] = "development"
    # Secret: carries the Postgres password.
    database_url: SecretStr = SecretStr(_DEV_DATABASE_URL)
    supabase_url: str = ""
    # `SUPABASE_PUBLISHABLE_KEY` is deliberately NOT a field here. It is the anon
    # key, and the only thing that reads it is tests/conftest.py, signing in to
    # mint a real token — it does so straight from the environment. There is no
    # Supabase Python client in this app: DB access is asyncpg on `database_url`
    # and auth verifies against the JWKS endpoint built from `supabase_url`, so
    # nothing in `app/` ever needed the key. Declaring it here only meant every
    # production deploy shipped a variable with no reader.

    # Airtable
    airtable_api_key: SecretStr = SecretStr("")
    airtable_base_id: str = ""            # e.g. appntjko6fQEC8Mnk
    airtable_clients_table_id: str = ""   # e.g. tblk0yEaixNQAd3Ij
    airtable_projects_table_id: str = ""  # e.g. tblqIWj0OJTUcj0yr
    airtable_revenue_table_id: str = ""   # e.g. tblxL8zHuKuIgqCew

    # Harvest
    harvest_token: SecretStr = SecretStr("")
    harvest_account_id: str = ""          # e.g. 123456
    harvest_user_agent_contact: str = ""  # e.g. example@email.com - Required on every request
    harvest_base_uri: str = ""            # e.g. https://client.harvestapp.com

    # Forecast (uses same bearer token as Harvest)
    forecast_account_id: str = ""         # e.g. 123456

    # LLM Providers
    openai_api_key: SecretStr = SecretStr("")

    # Stored as str so pydantic-settings doesn't try to JSON-decode it.
    # Reads from ALLOWED_ORIGINS env var (comma-separated).
    allowed_origins_raw: str = Field(
        _DEV_ALLOWED_ORIGINS,
        validation_alias=AliasChoices("ALLOWED_ORIGINS", "allowed_origins_raw"),
    )

    @computed_field
    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins_raw.split(",") if o.strip()]


def guard_test_db(cfg: Settings) -> None:
    """Refuse to run the suite against a non-test database.

    Deliberately *not* a `@model_validator`. A validator raises `ValueError`,
    which pydantic wraps in a `ValidationError` — and that carries an
    `input_value=` field holding the entire raw input dict, credentials included.
    Redacting the message would not have helped: the leak is in the wrapper, not
    the message. Raised from plain Python, the only thing printed is the string
    below.
    """
    if cfg.env != "test":
        return
    dsn = cfg.database_url.get_secret_value()
    url = dsn.lower()
    if "54323" not in url and "test" not in url:
        raise RuntimeError(
            f"ENV=test but DATABASE_URL='{_redact_dsn(dsn)}' does not point to a "
            "test database (expected '54323' or 'test' in the URL). "
            "Refusing to start — this would pollute the real database. "
            "Ensure TEST_DATABASE_URL is set and conftest.py overrides DATABASE_URL."
        )


def guard_production_config(cfg: Settings) -> list[str]:
    """Refuse to boot in production on a development default.

    Every field on `Settings` has a default, which is right for local dev and
    wrong for a container. A missing `DATABASE_URL` used to mean the app started,
    reported healthy, and failed on the first query; a missing `ALLOWED_ORIGINS`
    meant every browser request was CORS-blocked with no startup signal at all.
    Both are now a failed revision, which is the only failure mode you find
    without a user reporting it.

    Deliberately *not* a `@model_validator`, for the reason spelled out on
    `guard_test_db`: a validator's `ValueError` is wrapped in a `ValidationError`
    carrying `input_value=` — the whole environment. Nothing below interpolates a
    value, only variable names, so the message is safe to print anywhere.

    Returns the non-fatal warnings so the caller can log them once logging is
    configured; `config.py` is imported before `logging.basicConfig` runs.
    """
    if cfg.env != "production":
        return []

    problems: list[str] = []

    dsn = cfg.database_url.get_secret_value()
    if not dsn or dsn == _DEV_DATABASE_URL:
        problems.append("DATABASE_URL is unset or still the local-dev default")
    elif any(host in dsn for host in _LOCAL_HOSTS):
        problems.append("DATABASE_URL points at a local host")

    if not cfg.supabase_url:
        problems.append(
            "SUPABASE_URL is empty — the JWKS endpoint is built from it, so every "
            "authenticated request would 500"
        )
    elif not cfg.supabase_url.startswith("https://"):
        problems.append("SUPABASE_URL must be https:// in production")

    if not cfg.allowed_origins or cfg.allowed_origins_raw.strip() == _DEV_ALLOWED_ORIGINS:
        problems.append(
            "ALLOWED_ORIGINS is unset or still the local-dev default — the browser "
            "app would be CORS-blocked on every request"
        )
    elif any(origin.startswith("http://") for origin in cfg.allowed_origins):
        problems.append("ALLOWED_ORIGINS contains a plain-http origin")

    if not cfg.openai_api_key.get_secret_value():
        problems.append("OPENAI_API_KEY is empty — chat fails on the first message")

    # Invoicing is the live module: app/routers/billing.py and the whole of
    # app/services/billing/ reach Harvest. The contact header is not optional
    # padding — Harvest rejects requests without it.
    for name, value in (
        ("HARVEST_TOKEN", cfg.harvest_token.get_secret_value()),
        ("HARVEST_ACCOUNT_ID", cfg.harvest_account_id),
        ("HARVEST_USER_AGENT_CONTACT", cfg.harvest_user_agent_contact),
    ):
        if not value:
            problems.append(f"{name} is empty — invoicing fails on the first Harvest call")

    if problems:
        raise RuntimeError(
            "ENV=production but the configuration is incomplete:\n  - "
            + "\n  - ".join(problems)
            + "\n\nSet these as environment variables on the container app. "
            "Values are never echoed here, by design."
        )

    # Warn, don't fail. Each of these breaks one feature rather than the app, and
    # a deploy that refuses to start over a parked integration is its own outage.
    warnings: list[str] = []
    if not cfg.airtable_api_key.get_secret_value() or not cfg.airtable_base_id:
        warnings.append(
            "AIRTABLE_API_KEY/AIRTABLE_BASE_ID empty — the revenue-ops agent's "
            "get_revenue_data tool will fail when asked"
        )
    if not cfg.harvest_base_uri:
        warnings.append(
            "HARVEST_BASE_URI empty — invoice screens render without links back "
            "to Harvest (deliberate degradation, not a failure)"
        )
    return warnings


def _load_settings() -> Settings:
    """Build Settings, converting any validation failure into a safe message.

    `ValidationError.__str__` includes `input_value=` — every value pydantic was
    handed, which for this class means the whole environment. A single bad value
    would print every credential alongside it. Only field locations and messages
    are re-raised, and `from None` drops the original so the traceback cannot
    reintroduce it.
    """
    try:
        cfg = Settings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<model>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise RuntimeError(f"Invalid configuration — {problems}") from None
    guard_test_db(cfg)
    # Held rather than logged: this module is imported before logging is
    # configured, so `app/main.py` emits them once it is.
    config_warnings.extend(guard_production_config(cfg))
    return cfg


# Populated by `_load_settings`; drained by `app/main.py` after logging setup.
config_warnings: list[str] = []


settings = _load_settings()
