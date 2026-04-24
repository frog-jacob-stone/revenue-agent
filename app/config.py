from pydantic import AliasChoices, Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    supabase_url: str = ""
    supabase_secret_key: str = ""
    supabase_publishable_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    hubspot_token: str = ""
    apollo_api_key: str = ""
    log_level: str = "INFO"

    # Airtable
    airtable_api_key: str = ""
    airtable_base_id: str = ""            # e.g. appntjko6fQEC8Mnk
    airtable_clients_table_id: str = ""   # e.g. tblk0yEaixNQAd3Ij
    airtable_projects_table_id: str = ""  # e.g. tblqIWj0OJTUcj0yr
    airtable_revenue_table_id: str = ""   # e.g. tblxL8zHuKuIgqCew

    # Harvest
    harvest_token: str = ""
    harvest_account_id: str = ""          # e.g. 1560653

    # Forecast (uses same bearer token as Harvest)
    forecast_account_id: str = ""         # e.g. 1967278

    # Stored as str so pydantic-settings doesn't try to JSON-decode it.
    # Reads from ALLOWED_ORIGINS env var (comma-separated).
    allowed_origins_raw: str = Field(
        "http://localhost:3000,http://127.0.0.1:3000",
        validation_alias=AliasChoices("ALLOWED_ORIGINS", "allowed_origins_raw"),
    )

    @computed_field
    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins_raw.split(",") if o.strip()]

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
