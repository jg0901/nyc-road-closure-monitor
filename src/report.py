"""Compact run report: JSON (machine) + Markdown (human / n8n digest later).

Status is explicit. A failed fetch produces a FAILURE report — never a success
report with zero rows.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def build_report(run_id: str, status: str, *, retrieval: dict[str, Any] | None = None,
                 quality: dict[str, Any] | None = None, diff: dict[str, Any] | None = None,
                 run_entry: dict[str, Any] | None = None, compared_to: str | None = None,
                 error: str | None = None, log_path: Path | None = None,
                 warnings: list[str] | None = None) -> dict[str, Any]:
    meta = (retrieval or {}).get("dataset_metadata") or {}
    return {
        "run_id": run_id,
        "status": status,                      # success | failure
        "error": error,
        "warnings": warnings or [],
        "source": {
            "source_id": (retrieval or {}).get("source_id"),
            "source_url": (retrieval or {}).get("source_url"),
            "dataset_name": meta.get("name"),
            "source_rows_updated_at_utc": meta.get("rows_updated_at_utc"),  # source time
            "fetched_at_utc": (retrieval or {}).get("fetched_at_utc"),       # observation time
            "pages": (retrieval or {}).get("pages"),
        },
        "counts": {
            "rows_fetched": (retrieval or {}).get("row_count"),
            "rows_normalized": (quality or {}).get("row_count"),
            "diff": (diff or {}).get("counts"),
        },
        "quality": quality,
        "field_change_counts": (diff or {}).get("field_change_counts"),
        "snapshot": run_entry,
        "compared_to_run_id": compared_to,
        "log_path": str(log_path) if log_path else None,
    }


def to_markdown(report: dict[str, Any], diff: dict[str, Any] | None = None, sample: int = 10) -> str:
    s = report["source"]
    c = report["counts"]
    lines = [
        f"# Run {report['run_id']} — {report['status'].upper()}",
        "",
        f"- Dataset: {s.get('dataset_name') or s.get('source_id')}",
        f"- Source URL: {s.get('source_url')}",
        f"- Source last updated (UTC): {s.get('source_rows_updated_at_utc') or 'n/a'}",
        f"- Fetched at (UTC): {s.get('fetched_at_utc') or 'n/a'}",
        f"- Rows fetched: {c.get('rows_fetched')}  |  normalized: {c.get('rows_normalized')}",
        f"- Log: {report.get('log_path') or 'n/a'}",
    ]
    if report.get("error"):
        lines += ["", f"**Error:** `{report['error']}`"]
    if report.get("warnings"):
        lines += ["", "## Warnings"] + [f"- {w}" for w in report["warnings"]]
    if c.get("diff"):
        d = c["diff"]
        lines += ["", f"## Changes vs {report.get('compared_to_run_id') or 'first run'}",
                  f"| added | modified | unchanged | missing |", "|---|---|---|---|",
                  f"| {d['added']} | {d['modified']} | {d['unchanged']} | {d['missing']} |"]
        if report.get("field_change_counts"):
            lines += ["", "Fields changed:"] + [f"- `{f}`: {n}" for f, n in
                                                 sorted(report["field_change_counts"].items(), key=lambda x: -x[1])]
    if diff:
        for cat in ("added", "modified", "missing"):
            items = diff.get(cat) or []
            if items:
                lines += ["", f"### {cat} (showing {min(sample, len(items))} of {len(items)})"]
                for it in items[:sample]:
                    body = it.get("changes") if cat == "modified" else it.get("fields")
                    lines.append(f"- `{it['key']}` {json.dumps(body, ensure_ascii=False, default=str)}")
    q = report.get("quality") or {}
    if q:
        lines += ["", "## Quality",
                  f"- key check ok: {q.get('ok')}  |  duplicate keys: {q.get('duplicate_key_count')}  "
                  f"|  null-key rows: {q.get('null_key_rows')}",
                  f"- missing required: {q.get('missing_required')}",
                  f"- unparseable dates: {q.get('unparseable_dates')}",
                  f"- absent configured fields: {q.get('absent_fields')}"]
    lines += ["", "_Note: 'missing' means not present in this snapshot, not that the road reopened._"]
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], report_dir: Path, diff: dict[str, Any] | None = None) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    jp = report_dir / f"{report['run_id']}.report.json"
    mp = report_dir / f"{report['run_id']}.report.md"
    payload = dict(report)
    if diff is not None:
        payload["diff"] = diff
    jp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    mp.write_text(to_markdown(report, diff), encoding="utf-8")
    # Always-current pointer for schedulers / n8n.
    (report_dir / "latest.report.json").write_text(jp.read_text(encoding="utf-8"), encoding="utf-8")
    (report_dir / "latest.report.md").write_text(mp.read_text(encoding="utf-8"), encoding="utf-8")
    log.info("report written: %s", jp)
    return jp, mp
