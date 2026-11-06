"""Single-command pipeline: fetch -> normalize -> snapshot -> compare -> report.

Usage:
    python -m src.run --config config/settings.json
    python -m src.run --config config/settings.json --from-raw data/raw/<run_id>.raw.json.gz   # replay, no network
    python -m src.run --config config/settings.json --key-check uniqueid segmentid              # Friday key audit

Exit codes: 0 success, 1 failure (fetch/normalize/quality). A failure always
writes a failure report; it never writes an empty snapshot.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import compare, ingest, normalize, report, snapshot
from .config import Settings, load_settings

log = logging.getLogger("nyc_mobility_lab")


def make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def setup_logging(log_dir: Path, run_id: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{run_id}.log"
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt,
                        handlers=[logging.FileHandler(path, encoding="utf-8"),
                                  logging.StreamHandler(sys.stderr)])
    return path


def volume_check(settings: Settings, manifest: dict[str, Any], row_count: int) -> list[str]:
    """Suspicious blank / big-swing detection. Returns warnings (first is fatal if rows < min)."""
    warns: list[str] = []
    if row_count < settings.quality.min_rows:
        warns.append(f"FATAL: only {row_count} rows fetched (min_rows={settings.quality.min_rows}); treating as failed pull")
        return warns
    latest = snapshot.latest_snapshot_entry(manifest)
    if latest and latest.get("row_count"):
        delta = abs(row_count - latest["row_count"]) / latest["row_count"] * 100
        if delta > settings.quality.max_row_delta_pct:
            warns.append(f"row count moved {delta:.1f}% vs last snapshot ({latest['row_count']} -> {row_count})")
    return warns


def run(settings: Settings, session: Any = None, from_raw: Path | None = None,
        run_id: str | None = None) -> int:
    run_id = run_id or make_run_id()
    log_path = setup_logging(settings.paths.log_dir, run_id)
    log.info("run %s start", run_id)
    raw_path: Path | None = None

    # 1. Fetch (or replay)
    try:
        if from_raw:
            payload = ingest.load_raw(from_raw)
            retrieval, rows = payload["retrieval"], payload["rows"]
            raw_path = from_raw
            log.info("replaying raw file %s (%d rows)", from_raw, len(rows))
        else:
            if session is None:
                import requests
                session = requests.Session()
            result = ingest.fetch_all(session, settings.source)
            retrieval, rows = result.retrieval_metadata(), result.rows
            raw_path = ingest.save_raw(result, settings.paths.raw_dir, run_id)
    except ingest.FetchError as e:
        return _fail(settings, run_id, f"fetch failed: {e}", log_path)

    # 2. Volume sanity (before touching the manifest)
    manifest = snapshot.load_manifest(settings.paths.manifest)
    warnings = volume_check(settings, manifest, len(rows))
    if warnings and warnings[0].startswith("FATAL"):
        return _fail(settings, run_id, warnings[0], log_path, retrieval=retrieval)

    # 3. Normalize + quality
    try:
        normalized, qr = normalize.normalize_rows(rows, settings.schema)
    except normalize.NormalizeError as e:
        return _fail(settings, run_id, f"normalize failed: {e}", log_path, retrieval=retrieval)
    quality = qr.to_dict()
    if not qr.ok:
        warnings.append(f"quality checks flagged issues: dup_keys={len(qr.duplicate_keys)} "
                        f"missing_required={qr.missing_required}")

    # 4. Persist snapshot (idempotent) + 5. Compare to previous
    prev_entry = snapshot.latest_snapshot_entry(manifest)
    run_entry = snapshot.persist(normalized, retrieval, quality, settings.paths.snapshot_dir,
                                 settings.paths.manifest, run_id, raw_path)
    prev_rows = snapshot.load_snapshot(Path(prev_entry["path"])) if prev_entry else None
    diff = compare.compare(prev_rows, normalized)

    # 6. Report
    rep = report.build_report(run_id, "success", retrieval=retrieval, quality=quality,
                              diff=diff.to_dict(), run_entry=run_entry,
                              compared_to=prev_entry["run_id"] if prev_entry else None,
                              log_path=log_path, warnings=warnings)
    report.write_report(rep, settings.paths.report_dir, diff.to_dict())
    log.info("run %s success: %s", run_id, diff.counts)
    return 0


def _fail(settings: Settings, run_id: str, error: str, log_path: Path,
          retrieval: dict[str, Any] | None = None) -> int:
    log.error(error)
    manifest = snapshot.load_manifest(settings.paths.manifest)
    snapshot.record_run(manifest, run_id, "failure", error=error, snapshot_written=False)
    snapshot.save_manifest(settings.paths.manifest, manifest)
    rep = report.build_report(run_id, "failure", retrieval=retrieval, error=error, log_path=log_path)
    report.write_report(rep, settings.paths.report_dir)
    return 1


def key_check(settings: Settings, candidates: list[str], from_raw: Path | None, session: Any = None) -> int:
    """Audit candidate key fields on a live pull or a raw file. Prints a table."""
    if from_raw:
        rows = ingest.load_raw(from_raw)["rows"]
    else:
        if session is None:
            import requests
            session = requests.Session()
        rows = ingest.fetch_all(session, settings.source).rows
    rep = normalize.key_candidates_report(rows, candidates)
    print(f"{'field':<20}{'rows':>8}{'non_null':>10}{'distinct':>10}  unique?")
    for f, r in rep.items():
        print(f"{f:<20}{r['rows']:>8}{r['non_null']:>10}{r['distinct']:>10}  {r['is_unique']}")
    composite = normalize.key_candidates_report(
        [{"__c": "|".join(str(x.get(c)) for c in candidates)} for x in rows], ["__c"])["__c"]
    print(f"{'+'.join(candidates):<20}{composite['rows']:>8}{composite['non_null']:>10}"
          f"{composite['distinct']:>10}  {composite['is_unique']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NYC road-closure collector")
    p.add_argument("--config", default="config/settings.json")
    p.add_argument("--root", default=".", help="project root for relative data paths")
    p.add_argument("--from-raw", type=Path, help="replay a saved raw file instead of fetching")
    p.add_argument("--run-id", help="override run id (tests / replays)")
    p.add_argument("--key-check", nargs="+", metavar="FIELD", help="audit candidate key fields and exit")
    a = p.parse_args(argv)

    settings = load_settings(a.config, root=a.root)
    if a.key_check:
        return key_check(settings, a.key_check, a.from_raw)
    return run(settings, from_raw=a.from_raw, run_id=a.run_id)


if __name__ == "__main__":
    sys.exit(main())
