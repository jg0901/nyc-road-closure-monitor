"""Socrata (SODA) ingestion: pagination, bounded retries, timeouts, response metadata.

Contract:
- fetch_all() returns every row plus retrieval metadata, or raises FetchError.
- A failed or suspicious pull NEVER returns an empty list silently.
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .config import SourceConfig

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Raised when the source could not be retrieved completely and reliably."""


class _Response(Protocol):
    status_code: int
    text: str

    def json(self) -> Any: ...


class _Session(Protocol):
    """Minimal subset of requests.Session used here; lets tests inject a fake."""

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            timeout: float | None = None) -> _Response: ...


@dataclass
class FetchResult:
    rows: list[dict[str, Any]]
    source_id: str
    source_url: str
    dataset_id: str
    fetched_at_utc: str
    row_count: int
    pages: int
    dataset_metadata: dict[str, Any] = field(default_factory=dict)
    request_log: list[dict[str, Any]] = field(default_factory=list)

    def retrieval_metadata(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_url": self.source_url,
            "dataset_id": self.dataset_id,
            "fetched_at_utc": self.fetched_at_utc,
            "row_count": self.row_count,
            "pages": self.pages,
            "dataset_metadata": self.dataset_metadata,
            "request_log": self.request_log,
        }


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _headers(cfg: SourceConfig) -> dict[str, str]:
    h = {"Accept": "application/json", "User-Agent": "nyc-mobility-lab/0.1"}
    if cfg.app_token:
        h["X-App-Token"] = cfg.app_token
    return h


def _get_with_retries(session: _Session, url: str, params: dict | None, cfg: SourceConfig,
                      request_log: list[dict[str, Any]]) -> Any:
    """GET with bounded retries. Returns parsed JSON or raises FetchError."""
    last_err: str | None = None
    for attempt in range(1, cfg.max_retries + 1):
        started = time.monotonic()
        try:
            resp = session.get(url, params=params, headers=_headers(cfg), timeout=cfg.timeout_seconds)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            request_log.append({"url": url, "params": params, "status": resp.status_code,
                                "attempt": attempt, "elapsed_ms": elapsed_ms})
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as e:
                    last_err = f"non-JSON body: {e}"
                    log.warning("attempt %d: %s", attempt, last_err)
            elif resp.status_code in RETRYABLE_STATUS:
                last_err = f"HTTP {resp.status_code}"
                log.warning("attempt %d: %s (retryable)", attempt, last_err)
            else:
                raise FetchError(f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")
        except FetchError:
            raise
        except Exception as e:  # timeouts, connection errors
            last_err = f"{type(e).__name__}: {e}"
            request_log.append({"url": url, "params": params, "status": None,
                                "attempt": attempt, "error": last_err})
            log.warning("attempt %d: %s", attempt, last_err)
        if attempt < cfg.max_retries:
            time.sleep(cfg.backoff_seconds * (2 ** (attempt - 1)))
    raise FetchError(f"gave up after {cfg.max_retries} attempts on {url}: {last_err}")


def fetch_dataset_metadata(session: _Session, cfg: SourceConfig,
                           request_log: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pull dataset-level metadata (name, rowsUpdatedAt, columns). Non-fatal if it fails."""
    request_log = request_log if request_log is not None else []
    try:
        meta = _get_with_retries(session, cfg.metadata_url, None, cfg, request_log)
    except FetchError as e:
        log.warning("dataset metadata unavailable: %s", e)
        return {}
    if not isinstance(meta, dict):
        return {}
    updated = meta.get("rowsUpdatedAt")
    return {
        "name": meta.get("name"),
        "rows_updated_at_epoch": updated,
        "rows_updated_at_utc": (
            datetime.fromtimestamp(updated, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if isinstance(updated, (int, float)) else None
        ),
        "columns": [c.get("fieldName") for c in meta.get("columns", []) if isinstance(c, dict)],
    }


def fetch_all(session: _Session, cfg: SourceConfig, include_system_fields: bool = True) -> FetchResult:
    """Fetch every page of the dataset. Raises FetchError on any unrecoverable problem."""
    fetched_at = _now_utc()
    request_log: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    offset, pages = 0, 0

    where = cfg.window.soda_where() if cfg.window else None
    if where:
        log.info("window filter: %s", where)

    while True:
        params: dict[str, Any] = {"$limit": cfg.page_size, "$offset": offset, "$order": cfg.order_by}
        if include_system_fields:
            params["$select"] = ":*,*"  # adds :id, :created_at, :updated_at
        if where:
            params["$where"] = where
        page = _get_with_retries(session, cfg.resource_url, params, cfg, request_log)
        if not isinstance(page, list):
            raise FetchError(f"expected JSON array at offset {offset}, got {type(page).__name__}")
        pages += 1
        rows.extend(page)
        log.info("page %d: %d rows (offset %d)", pages, len(page), offset)
        if len(page) < cfg.page_size:
            break
        offset += cfg.page_size

    metadata = fetch_dataset_metadata(session, cfg, request_log)

    metadata["window_where"] = where  # record the filter so a snapshot is interpretable later

    return FetchResult(
        rows=rows,
        source_id=cfg.source_id,
        source_url=cfg.resource_url,
        dataset_id=cfg.dataset_id,
        fetched_at_utc=fetched_at,
        row_count=len(rows),
        pages=pages,
        dataset_metadata=metadata,
        request_log=request_log,
    )


def save_raw(result: FetchResult, raw_dir: Path, run_id: str) -> Path:
    """Persist the raw response + retrieval metadata (gzipped JSON). Bronze layer."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    out = raw_dir / f"{run_id}.raw.json.gz"
    payload = {"retrieval": result.retrieval_metadata(), "rows": result.rows}
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    log.info("raw saved: %s (%d rows)", out, result.row_count)
    return out


def load_raw(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)
