from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    supabase_url: str = ""
    supabase_secret_key: str = ""
    supabase_publishable_key: str = ""
    anthropic_api_key: str = ""
    hubspot_token: str = ""
    apollo_api_key: str = ""
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _guard_test_db(self) -> "Settings":
        if self.env == "test":
            url = self.database_url.lower()
            if "54323" not in url and "test" not in url:
                raise ValueError(
                    f"ENV=test but DATABASE_URL='{self.database_url}' does not point to a test "
                    "database (expected '54323' or 'test' in the URL). "
                    "Refusing to start — this would pollute the real database. "
                    "Ensure TEST_DATABASE_URL is set and conftest.py overrides DATABASE_URL."
                )
        return self


settings = Settings()
