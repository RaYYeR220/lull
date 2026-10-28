from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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

    # Spend caps in USD on model calls: 0 refuses every call, "none" switches a cap off.
    lull_cap_run: float | None = Field(default=0.50, validation_alias="LULL_CAP_RUN")
    lull_cap_day: float | None = Field(default=3.00, validation_alias="LULL_CAP_DAY")
    lull_cap_total: float | None = Field(default=35.00, validation_alias="LULL_CAP_TOTAL")
    # Web searches (Tavily) are counted, not priced: at most this many per run and per day.
    lull_tavily_run: int | None = Field(default=5, validation_alias="LULL_TAVILY_RUN")
    lull_tavily_day: int | None = Field(default=60, validation_alias="LULL_TAVILY_DAY")

    @field_validator(
        "lull_cap_run", "lull_cap_day", "lull_cap_total", "lull_tavily_run", "lull_tavily_day",
        mode="before",
    )  # fmt: skip
    @classmethod
    def _none_is_no_cap(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() in ("none", ""):
            return None
        return value

    # Evaluation only: finalize skips claim verification and voices lines "unverified".
    lull_no_verify: bool = Field(default=False, validation_alias="LULL_NO_VERIFY")

    @field_validator("lull_no_verify", mode="before")
    @classmethod
    def _empty_is_off(cls, value: object) -> object:
        return False if isinstance(value, str) and not value.strip() else value

    # Ledger and voice calibration files live here.
    lull_home: Path = Field(
        default_factory=lambda: Path.home() / ".lull", validation_alias="LULL_HOME"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
