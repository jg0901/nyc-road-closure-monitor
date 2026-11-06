"""Snapshot persistence + manifest.

A snapshot = normalized rows for one successful run, stored as gzipped JSON.
The manifest is the durable index of snapshots and runs.

Idempotency rule: a run whose snapshot_hash equals the latest snapshot's hash
records a *run* entry but does NOT write a new snapshot file or snapshot entry.
Re-running on unchanged data therefore never creates duplicate snapshot entries.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def snapshot_hash(normalized: list[dict[str, Any]]) -> str:
    """Order-independent hash of the whole dataset (keys + content hashes)."""
    pairs = sorted((r["key"], r["content_hash"]) for r in normalized)
    canon = json.dumps(pairs, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"snapshots": [], "runs": []}


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def latest_snapshot_entry(manifest: dict[str, Any]) -> dict[str, Any] | None:
    return manifest["snapshots"][-1] if manifest["snapshots"] else None


def write_snapshot(normalized: list[dict[str, Any]], snapshot_dir: Path, run_id: str) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    out = snapshot_dir / f"{run_id}.snapshot.json.gz"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False)
    return out


def load_snapshot(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def record_run(manifest: dict[str, Any], run_id: str, status: str, **extra: Any) -> None:
    manifest["runs"].append({"run_id": run_id, "status": status, **extra})


def persist(normalized: list[dict[str, Any]], retrieval: dict[str, Any], quality: dict[str, Any],
            snapshot_dir: Path, manifest_path: Path, run_id: str, raw_path: Path | None) -> dict[str, Any]:
    """Write snapshot if content changed; always record the run. Returns the run entry."""
    manifest = load_manifest(manifest_path)
    h = snapshot_hash(normalized)
    latest = latest_snapshot_entry(manifest)

    if latest and latest["snapshot_hash"] == h:
        entry = {"run_id": run_id, "status": "success", "snapshot_written": False,
                 "deduplicated_to": latest["run_id"], "snapshot_hash": h,
                 "row_count": len(normalized), "fetched_at_utc": retrieval.get("fetched_at_utc"),
                 "raw_path": str(raw_path) if raw_path else None}
        log.info("dataset unchanged since %s; no new snapshot written", latest["run_id"])
    else:
        path = write_snapshot(normalized, snapshot_dir, run_id)
        snap = {"run_id": run_id, "snapshot_hash": h, "path": str(path),
                "row_count": len(normalized),
                "fetched_at_utc": retrieval.get("fetched_at_utc"),
                "source_rows_updated_at_utc": (retrieval.get("dataset_metadata") or {}).get("rows_updated_at_utc"),
                "raw_path": str(raw_path) if raw_path else None,
                "quality": quality}
        manifest["snapshots"].append(snap)
        entry = {"run_id": run_id, "status": "success", "snapshot_written": True,
                 "snapshot_hash": h, "row_count": len(normalized),
                 "fetched_at_utc": retrieval.get("fetched_at_utc"),
                 "raw_path": str(raw_path) if raw_path else None}
        log.info("snapshot written: %s", path)

    manifest["runs"].append(entry)
    save_manifest(manifest_path, manifest)
    return entry


def previous_two(manifest: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(previous, latest) snapshot entries for diffing. Either may be None."""
    s = manifest["snapshots"]
    if len(s) >= 2:
        return s[-2], s[-1]
    if len(s) == 1:
        return None, s[-1]
    return None, None
