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
    """One NYC DOT advisory HTML page (weekly, weekend, ...). Separate source, separate history per page."""
    name: str                                   # "weekly" | "weekend" — goes into every record key
    url: str
    base_dir: Path = Path("data/advisory")      # history lives in <base_dir>/<name>/
    log_root: Path = Path("logs/advisory")
    content_selector: str = "#_content"
    timeout_seconds: int = 30
    max_retries: int = 4
    backoff_seconds: float = 2.0
    app_token: str | None = None  # never sent; present so the shared retry helper's duck-typing works

    @property
    def source_id(self) -> str:
        return f"nyc_dot_{self.name}_traffic_advisory"

    @property
    def raw_dir(self) -> Path: return self.base_dir / self.name / "raw"
    @property
    def snapshot_dir(self) -> Path: return self.base_dir / self.name / "snapshots"
    @property
    def report_dir(self) -> Path: return self.base_dir / self.name / "reports"
    @property
    def manifest(self) -> Path: return self.snapshot_dir / "manifest.json"
    @property
    def log_dir(self) -> Path: return self.log_root / self.name


@dataclass
class Settings:
    source: SourceConfig
    schema: SchemaConfig
    quality: QualityConfig
    paths: PathsConfig
    advisory: list[AdvisoryConfig] = field(default_factory=list)


def _strip_comments(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def load_settings(path: str | Path, root: str | Path | None = None) -> Settings:
    """Load settings JSON. Relative paths in `paths` resolve against `root` (default: cwd)."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    root = Path(root) if root else Path.cwd()

    paths_raw = _strip_comments(raw["paths"])
    paths = PathsConfig(**{k: (root / v) for k, v in paths_raw.items()})

    advisory: list[AdvisoryConfig] = []
    a = raw.get("advisory")
    if a and a.get("enabled", True):
        a = _strip_comments(a)
        shared = {k: v for k, v in a.items() if k not in ("enabled", "pages")}
        for k in ("base_dir", "log_root"):
            if k in shared:
                shared[k] = root / shared[k]
        for page in a.get("pages", []):
            page = _strip_comments(page)
            if not page.get("enabled", True):
                continue
            page.pop("enabled", None)
            advisory.append(AdvisoryConfig(**{**shared, **page}))
        for cfg in advisory:
            for k in ("base_dir", "log_root"):
                if not getattr(cfg, k).is_absolute():
                    setattr(cfg, k, root / getattr(cfg, k))

    return Settings(
        source=SourceConfig(**_strip_comments(raw["source"])),
        schema=SchemaConfig(**_strip_comments(raw["schema"])),
        quality=QualityConfig(**_strip_comments(raw.get("quality", {}))),
        paths=paths,
        advisory=advisory,
    )
