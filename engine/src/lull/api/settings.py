from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, populate_by_name=True
    )

    lull_data: Path = Field(
        default_factory=lambda: Path.home() / ".lull" / "data", validation_alias="LULL_DATA"
    )
    lull_web_origins: str = Field(default="", validation_alias="LULL_WEB_ORIGINS")
    # trusted proxies in front of the app (Cloud Run: 1); 0 = use the socket peer address
    lull_proxy_hops: int = Field(default=0, ge=0, validation_alias="LULL_PROXY_HOPS")
    # local only: lets the Studio change showcase runs (they are read-only when deployed)
    lull_showcase_editable: bool = Field(default=False, validation_alias="LULL_SHOWCASE_EDITABLE")

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.lull_web_origins.split(",") if o.strip()]

    @property
    def runs_dir(self) -> Path:
        return self.lull_data / "runs"

    @property
    def showcase_dir(self) -> Path:
        return self.lull_data / "showcase"

    @property
    def samples_dir(self) -> Path:
        return self.lull_data / "samples"
