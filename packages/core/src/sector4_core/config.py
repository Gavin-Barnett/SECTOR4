from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://sector4:sector4@db:5432/sector4"
    sec_user_agent: str = "SECTOR4/0.1 (dev@localhost)"
    sec_base_url: str = "https://www.sec.gov"
    sec_data_base_url: str = "https://data.sec.gov"
    sec_max_rps: int = 5
    openai_api_key: str | None = None
    ai_summary_model: str = "gpt-5.4-mini"
    market_data_provider: str | None = None
    market_data_api_key: str | None = None
    alert_webhook_url: str | None = None
    alert_min_signal_score: Decimal = Decimal("75")
    alert_min_score_delta: Decimal = Decimal("5")
    alert_min_total_purchase_delta_usd: Decimal = Decimal("50000")
    default_market_cap_max: int = 500000000
    default_cluster_window_days: int = 30
    default_min_unique_buyers: int = 2
    default_min_total_purchase_usd: int = 100000
    routine_micro_transaction_usd: int = 10000
    raw_filings_dir: str = "data/raw_filings"
    fixture_manifest_path: str = "tests/fixtures/sec/manifest.json"
    proxy_fixture_manifest_path: str = "tests/fixtures/sec/proxy_manifest.json"
    sec_proxy_sync_enabled: bool = False
    cors_allowed_origins: str = "http://localhost:5180,http://127.0.0.1:5180"
    ops_api_token: str | None = None
    ops_live_ingest_limit: int = 25
    ops_backfill_days: int = 5
    ops_poll_interval_seconds: int = 900
    ops_scheduler_enabled: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()