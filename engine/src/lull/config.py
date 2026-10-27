from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings read from the environment. Secrets are optional at import time."""

    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, populate_by_name=True
    )

    nebius_api_key: str | None = Field(default=None, validation_alias="NEBIUS_API_KEY")
    tavily_api_key: str | None = Field(default=None, validation_alias="TAVILY_API_KEY")
    lull_speech_url: str | None = Field(default=None, validation_alias="LULL_SPEECH_URL")
    lull_speech_key: str | None = Field(default=None, validation_alias="LULL_SPEECH_KEY")

    # Spend caps in USD.
    lull_cap_run: float = Field(default=0.50, validation_alias="LULL_CAP_RUN")
    lull_cap_day: float = Field(default=3.00, validation_alias="LULL_CAP_DAY")
    lull_cap_total: float = Field(default=35.00, validation_alias="LULL_CAP_TOTAL")

    # Ledger and voice calibration files live here.
    lull_home: Path = Field(
        default_factory=lambda: Path.home() / ".lull", validation_alias="LULL_HOME"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
