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
    lull_samples: Path | None = Field(default=None, validation_alias="LULL_SAMPLES")
    # trusted proxies in front of the app (Cloud Run: 1); 0 = use the socket peer address
    lull_proxy_hops: int = Field(default=0, ge=0, validation_alias="LULL_PROXY_HOPS")
    # local only: lets the Studio change showcase runs (they are read-only when deployed)
    lull_showcase_editable: bool = Field(default=False, validation_alias="LULL_SHOWCASE_EDITABLE")
    # how long an upload and its run are kept (forks: a day); the site's notice states it
    lull_upload_retention_days: float = Field(
        default=7.0, gt=0, validation_alias="LULL_UPLOAD_RETENTION_DAYS"
    )
    # how long a live run may take before it is given up (lull.api.live)
    lull_run_wall_clock_s: float = Field(
        default=900.0, gt=0, validation_alias="LULL_RUN_WALL_CLOCK_S"
    )

    # the API's public address(es), comma-separated: the first makes the links MCP get_tracks
    # hands out; all are the hosts /mcp answers to (with localhost). Unset: links are relative
    # and /mcp answers localhost only
    lull_public_url: str = Field(default="", validation_alias="LULL_PUBLIC_URL")

    @property
    def public_urls(self) -> list[str]:
        return [u.strip().rstrip("/") for u in self.lull_public_url.split(",") if u.strip()]

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
        """LULL_SAMPLES (the image bakes the licensed samples in), else LULL_DATA/samples."""
        return self.lull_samples or self.lull_data / "samples"
