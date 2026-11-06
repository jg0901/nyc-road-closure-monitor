"""Load pipeline settings from JSON, with env overrides for secrets."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WindowConfig:
    """Fetch only rows whose [start_field, end_field] permit window overlaps [start, end]."""
    start: str            # YYYY-MM-DD
    end: str              # YYYY-MM-DD (inclusive)
    start_field: str = "work_start_date"
    end_field: str = "work_end_date"

    def soda_where(self) -> str:
        # Overlap test: permit ends on/after window start AND permit starts on/before window end.
        # Socrata floating timestamps compare correctly against ISO literals.
        return (f"{self.end_field} >= '{self.start}T00:00:00.000' AND "
                f"{self.start_field} <= '{self.end}T23:59:59.999'")


@dataclass
class SourceConfig:
    source_id: str
    domain: str
    dataset_id: str
    page_size: int = 1000
    order_by: str = ":id"
    timeout_seconds: int = 30
    max_retries: int = 4
    backoff_seconds: float = 2.0
    app_token_env: str = "SOCRATA_APP_TOKEN"
    window: WindowConfig | None = None

    def __post_init__(self) -> None:
        if isinstance(self.window, dict):
            self.window = WindowConfig(**_strip_comments(self.window))

    @property
    def resource_url(self) -> str:
        return f"{self.domain}/resource/{self.dataset_id}.json"

    @property
    def metadata_url(self) -> str:
        return f"{self.domain}/api/views/{self.dataset_id}.json"

    @property
    def app_token(self) -> str | None:
        return os.environ.get(self.app_token_env) or None


@dataclass
class SchemaConfig:
    key_fields: list[str]
    business_fields: list[str]
    date_fields: list[str] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)
    fail_on_duplicate_keys: bool = False


@dataclass
class QualityConfig:
    min_rows: int = 1
    max_row_delta_pct: float = 50.0


@dataclass
class PathsConfig:
    raw_dir: Path
    snapshot_dir: Path
    report_dir: Path
    manifest: Path
    log_dir: Path


@dataclass
class AdvisoryConfig:
    """Secondary adapter: NYC DOT Weekly Traffic Advisory (HTML). Separate source, separate history."""
    url: str = "https://www.nyc.gov/html/dot/html/motorist/weektraf.shtml"
    source_id: str = "nyc_dot_weekly_traffic_advisory"
    content_selector: str = "#_content"
    timeout_seconds: int = 30
    max_retries: int = 4
    backoff_seconds: float = 2.0
    enabled: bool = True
    app_token: str | None = None  # never sent; present so the shared retry helper's duck-typing works
    raw_dir: Path = Path("data/advisory/raw")
    snapshot_dir: Path = Path("data/advisory/snapshots")
    report_dir: Path = Path("data/advisory/reports")
    manifest: Path = Path("data/advisory/snapshots/manifest.json")
    log_dir: Path = Path("logs/advisory")


@dataclass
class Settings:
    source: SourceConfig
    schema: SchemaConfig
    quality: QualityConfig
    paths: PathsConfig
    advisory: AdvisoryConfig | None = None


def _strip_comments(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def load_settings(path: str | Path, root: str | Path | None = None) -> Settings:
    """Load settings JSON. Relative paths in `paths` resolve against `root` (default: cwd)."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    root = Path(root) if root else Path.cwd()

    paths_raw = _strip_comments(raw["paths"])
    paths = PathsConfig(**{k: (root / v) for k, v in paths_raw.items()})

    advisory = None
    if "advisory" in raw and raw["advisory"] is not None:
        a = _strip_comments(raw["advisory"])
        for k in ("raw_dir", "snapshot_dir", "report_dir", "manifest", "log_dir"):
            if k in a:
                a[k] = root / a[k]
        advisory = AdvisoryConfig(**a)
        for k in ("raw_dir", "snapshot_dir", "report_dir", "manifest", "log_dir"):
            p = getattr(advisory, k)
            if not p.is_absolute():
                setattr(advisory, k, root / p)

    return Settings(
        source=SourceConfig(**_strip_comments(raw["source"])),
        schema=SchemaConfig(**_strip_comments(raw["schema"])),
        quality=QualityConfig(**_strip_comments(raw.get("quality", {}))),
        paths=paths,
        advisory=advisory,
    )
