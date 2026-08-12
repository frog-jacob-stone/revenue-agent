"""Credentials must not survive a `repr()` of Settings.

This exists because it happened twice. Pydantic prints every field value by
default, so an unrelated validation error — or any failing test whose assertion
output rendered a Settings object — dumped the live Harvest token, OpenAI key,
and Airtable PAT into terminal output. Once into a shared transcript.

`SecretStr` fixes it, but only for the fields that use it, and adding a plain
`str` credential later would silently reopen the hole. These tests fail if that
happens.
"""
import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from app.config import (
    Settings,
    _load_settings,
    _redact_dsn,
    guard_production_config,
    guard_test_db,
)

# A production config with nothing missing. Spelled out in full rather than
# built by mutation, so each test states the one thing it is changing — and
# passed explicitly so the ambient app/.env cannot make a test pass by accident.
_COMPLETE_PRODUCTION = {
    "env": "production",
    "database_url": "postgresql://u:p@db.example.com:5432/app",
    "supabase_url": "https://ref.supabase.co",
    "allowed_origins_raw": "https://revops.frogslayer.com",
    "openai_api_key": "sk-x",
    "harvest_token": "t",
    "harvest_account_id": "123456",
    "harvest_user_agent_contact": "ops@example.com",
}

# Distinctive enough that a substring search cannot match by accident.
_CANARY = "zzz-canary-value-do-not-log-9f3a1b"

# Every field that carries a credential. A new secret must be added here, and
# `test_all_secret_fields_are_secretstr` fails until it is wrapped.
SECRET_FIELDS = (
    "database_url",
    "airtable_api_key",
    "harvest_token",
    "openai_api_key",
)


def _settings_with_canaries() -> Settings:
    return Settings(**{f: _CANARY for f in SECRET_FIELDS})


def test_repr_does_not_contain_secrets():
    text = repr(_settings_with_canaries())
    assert _CANARY not in text
    # Sanity: the repr is not simply empty, so absence means redaction.
    assert "Settings(" in text


def test_str_does_not_contain_secrets():
    assert _CANARY not in str(_settings_with_canaries())


def test_model_dump_does_not_contain_secrets():
    """Covers logging patterns like `logger.info("config: %s", settings.model_dump())`."""
    assert _CANARY not in str(_settings_with_canaries().model_dump())


def test_all_secret_fields_are_secretstr():
    """A credential declared as a plain `str` reopens the leak."""
    for name in SECRET_FIELDS:
        annotation = Settings.model_fields[name].annotation
        assert annotation is SecretStr, (
            f"{name} must be SecretStr, got {annotation}. A plain str credential "
            "renders in full in any repr of Settings."
        )


def test_secrets_are_still_readable():
    """Redaction is only useful if the value still round-trips to the caller."""
    cfg = _settings_with_canaries()
    for name in SECRET_FIELDS:
        assert getattr(cfg, name).get_secret_value() == _CANARY


def test_redact_dsn_strips_credentials():
    out = _redact_dsn("postgresql://someuser:s3cr3t@db.example.com:5432/appdb")
    assert "s3cr3t" not in out
    assert "someuser" not in out
    # The diagnosable part survives — that is the whole point of the message.
    assert "db.example.com:5432/appdb" in out


def test_redact_dsn_passes_through_credential_free_urls():
    plain = "postgresql://127.0.0.1:54322/postgres"
    assert _redact_dsn(plain) == plain


def test_test_db_guard_message_does_not_leak_the_password():
    """The guard interpolates the DSN; it must interpolate the redacted one."""
    cfg = Settings(
        env="test",
        database_url=f"postgresql://u:{_CANARY}@prod-host:5432/live",
    )
    with pytest.raises(RuntimeError) as excinfo:
        guard_test_db(cfg)
    assert _CANARY not in str(excinfo.value)
    assert "prod-host" in str(excinfo.value)


def test_test_db_guard_accepts_a_test_dsn():
    """Sanity: the guard is not simply raising on everything."""
    guard_test_db(Settings(env="test", database_url="postgresql://u:p@h:54322/postgres_test"))


def test_production_guard_message_never_echoes_a_value():
    """The guard names variables; it must never print what they contain.

    `guard_test_db` can interpolate its DSN only because it runs it through
    `_redact_dsn` first. This guard has no such need, and this test is what keeps
    someone from "helpfully" adding `got {value}` to a message later.
    """
    cfg = Settings(
        env="production",
        database_url=f"postgresql://u:{_CANARY}@db.example.com:5432/app",
        supabase_url="https://ref.supabase.co",
        openai_api_key=_CANARY,
        harvest_token=_CANARY,
        # ALLOWED_ORIGINS left at the dev default, so the guard raises.
    )
    with pytest.raises(RuntimeError) as excinfo:
        guard_production_config(cfg)
    message = str(excinfo.value)
    assert _CANARY not in message
    # Still diagnosable: the failing variable is named.
    assert "ALLOWED_ORIGINS" in message


def test_production_guard_is_silent_outside_production():
    """Local dev and the test suite must not trip it — every default is a dev default."""
    assert guard_production_config(Settings()) == []
    assert guard_production_config(Settings(env="test")) == []


def test_production_guard_accepts_a_complete_production_config():
    """Sanity: the guard is not simply raising on everything."""
    assert guard_production_config(Settings(**_COMPLETE_PRODUCTION)) == []


@pytest.mark.parametrize(
    "override, expected_name",
    [
        (
            {"database_url": "postgresql://postgres:postgres@127.0.0.1:54322/postgres"},
            "DATABASE_URL",
        ),
        ({"database_url": "postgresql://u:p@localhost:5432/app"}, "DATABASE_URL"),
        ({"supabase_url": ""}, "SUPABASE_URL"),
        ({"supabase_url": "http://ref.supabase.co"}, "SUPABASE_URL"),
        ({"allowed_origins_raw": "http://localhost:3000,http://127.0.0.1:3000"}, "ALLOWED_ORIGINS"),
        ({"allowed_origins_raw": "http://revops.frogslayer.com"}, "ALLOWED_ORIGINS"),
        ({"openai_api_key": ""}, "OPENAI_API_KEY"),
        ({"harvest_token": ""}, "HARVEST_TOKEN"),
        ({"harvest_account_id": ""}, "HARVEST_ACCOUNT_ID"),
        ({"harvest_user_agent_contact": ""}, "HARVEST_USER_AGENT_CONTACT"),
    ],
)
def test_production_guard_catches_each_required_variable(override, expected_name):
    """Each of these is a variable whose absence used to produce a running app.

    The point of the guard is that a forgotten value fails the deploy rather than
    surfacing later as a mystery 500 or a silently CORS-blocked frontend.
    """
    cfg = Settings(**{**_COMPLETE_PRODUCTION, **override})
    with pytest.raises(RuntimeError) as excinfo:
        guard_production_config(cfg)
    assert expected_name in str(excinfo.value)


def test_production_guard_warns_rather_than_fails_on_parked_integrations():
    """Airtable breaks one tool, not the app. A deploy that refuses to start over
    a parked integration is its own outage."""
    warnings = guard_production_config(Settings(**_COMPLETE_PRODUCTION))
    assert warnings == []

    partial = Settings(**{**_COMPLETE_PRODUCTION, "airtable_api_key": "", "harvest_base_uri": ""})
    warnings = guard_production_config(partial)  # does not raise
    assert any("AIRTABLE" in w for w in warnings)
    assert any("HARVEST_BASE_URI" in w for w in warnings)


def test_invalid_env_value_is_rejected_without_leaking(monkeypatch):
    """`ENV=prod` must fail loudly, not silently disable the guard above.

    `env` is a Literal for exactly this reason. Pydantic's mismatch message lists
    the permitted values rather than the supplied one, and `_load_settings`
    sanitizes it regardless — this asserts both.
    """
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("OPENAI_API_KEY", _CANARY)
    with pytest.raises(RuntimeError) as excinfo:
        _load_settings()
    message = str(excinfo.value)
    assert _CANARY not in message
    assert "env" in message


def _fail_validation_carrying_the_canary(*_args, **_kwargs):
    """Raise a genuine ValidationError whose `input_value` is the canary."""

    class _Inner(BaseModel):
        some_field: int

    _Inner(some_field=_CANARY)


def test_a_raw_validation_error_really_does_leak_its_input():
    """The premise. If this ever stops holding, the sanitizer below is dead weight."""
    with pytest.raises(ValidationError) as excinfo:
        _fail_validation_carrying_the_canary()
    assert _CANARY in str(excinfo.value)


def test_validation_errors_do_not_leak_the_environment(monkeypatch):
    """One bad value must not print every other value alongside it.

    `ValidationError` renders `input_value=` — for Settings, that is everything
    pydantic was handed, so a single malformed field dumped every credential next
    to it. `_load_settings` re-raises locations and messages only.
    """
    monkeypatch.setattr("app.config.Settings", _fail_validation_carrying_the_canary)
    with pytest.raises(RuntimeError) as excinfo:
        _load_settings()
    message = str(excinfo.value)
    assert _CANARY not in message
    assert "Invalid configuration" in message
    # Still diagnosable: the failing field is named.
    assert "some_field" in message
