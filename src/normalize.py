"""Normalize raw Socrata rows: type conversions, key checks, canonical row fingerprint.

Each normalized row keeps:
- key            : stable key (joined key_fields)
- content_hash   : sha256 over canonicalized business fields only
- fields         : normalized business fields
- original       : the raw values for those fields (never lost)
- system         : Socrata system fields (:id, :created_at, :updated_at) when present
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config import SchemaConfig

log = logging.getLogger(__name__)

_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%Y %H:%M:%S")


class NormalizeError(ValueError):
    """Raised when the pull violates the schema contract hard enough to stop."""


@dataclass
class QualityReport:
    row_count: int = 0                 # raw rows in
    output_rows: int = 0               # rows after dedupe
    exact_duplicate_rows: int = 0      # same key AND same content_hash: source published the row twice
    duplicate_keys: dict[str, int] = field(default_factory=dict)   # key -> occurrences, DIFFERENT content (collision)
    missing_required: dict[str, int] = field(default_factory=dict)  # field -> rows missing
    unparseable_dates: dict[str, int] = field(default_factory=dict) # field -> rows failed
    unexpected_fields: list[str] = field(default_factory=list)      # in data, not in config
    absent_fields: list[str] = field(default_factory=list)          # in config, not in data
    null_key_rows: int = 0

    @property
    def ok(self) -> bool:
        return not self.duplicate_keys and not self.missing_required and self.null_key_rows == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "output_rows": self.output_rows,
            "ok": self.ok,
            "exact_duplicate_rows": self.exact_duplicate_rows,
            "duplicate_key_count": len(self.duplicate_keys),
            "duplicate_keys_sample": dict(list(self.duplicate_keys.items())[:10]),
            "null_key_rows": self.null_key_rows,
            "missing_required": self.missing_required,
            "unparseable_dates": self.unparseable_dates,
            "unexpected_fields": self.unexpected_fields,
            "absent_fields": self.absent_fields,
        }


def norm_text(v: Any) -> str | None:
    """Trim, collapse whitespace, uppercase. Returns None for blank/None."""
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s.upper() or None


def parse_date(v: Any) -> str | None:
    """Return ISO date (YYYY-MM-DD) or None if unparseable/blank."""
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def build_key(row: dict[str, Any], key_fields: list[str]) -> str | None:
    parts = [norm_text(row.get(f)) for f in key_fields]
    if any(p is None for p in parts):
        return None
    return "|".join(parts)


def content_hash(fields: dict[str, Any]) -> str:
    """Deterministic hash: sorted keys, canonical JSON, no whitespace."""
    canon = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def normalize_row(row: dict[str, Any], schema: SchemaConfig, qr: QualityReport) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    original: dict[str, Any] = {}
    for f in schema.business_fields:
        raw_v = row.get(f)
        original[f] = raw_v
        if f in schema.date_fields:
            parsed = parse_date(raw_v)
            if raw_v not in (None, "") and parsed is None:
                qr.unparseable_dates[f] = qr.unparseable_dates.get(f, 0) + 1
            fields[f] = parsed
        else:
            fields[f] = norm_text(raw_v)

    for f in schema.required_fields:
        if row.get(f) in (None, ""):
            qr.missing_required[f] = qr.missing_required.get(f, 0) + 1

    key = build_key(row, schema.key_fields)
    system = {k: v for k, v in row.items() if k.startswith(":")}
    return {
        "key": key,
        "content_hash": content_hash(fields),
        "fields": fields,
        "original": original,
        "system": system,
    }


def normalize_rows(rows: list[dict[str, Any]], schema: SchemaConfig) -> tuple[list[dict[str, Any]], QualityReport]:
    """Normalize all rows and run key/schema checks.

    Raises NormalizeError only for null keys or (if configured) duplicate keys.
    Everything else is reported, not fatal.
    """
    qr = QualityReport(row_count=len(rows))

    seen_fields: set[str] = set()
    for r in rows:
        seen_fields.update(k for k in r.keys() if not k.startswith(":"))
    configured = set(schema.business_fields) | set(schema.key_fields)
    qr.unexpected_fields = sorted(seen_fields - configured)
    qr.absent_fields = sorted(configured - seen_fields) if rows else []
    if qr.absent_fields:
        log.warning("configured fields absent from data (schema drift?): %s", qr.absent_fields)

    out: list[dict[str, Any]] = []
    seen: dict[str, str] = {}          # key -> content_hash of the row we kept
    collisions: dict[str, int] = {}
    for r in rows:
        n = normalize_row(r, schema, qr)
        if n["key"] is None:
            qr.null_key_rows += 1
            continue
        k = n["key"]
        if k in seen:
            if seen[k] == n["content_hash"]:
                qr.exact_duplicate_rows += 1      # source published identical row again: drop silently
            else:
                collisions[k] = collisions.get(k, 1) + 1  # same key, different content: keep first, flag
            continue
        seen[k] = n["content_hash"]
        out.append(n)

    qr.output_rows = len(out)
    qr.duplicate_keys = collisions
    if qr.exact_duplicate_rows:
        log.info("dropped %d exact duplicate rows (same key and content)", qr.exact_duplicate_rows)
    if qr.duplicate_keys:
        log.warning("%d keys collide with DIFFERENT content; key_fields=%s may not be a stable key",
                    len(qr.duplicate_keys), schema.key_fields)
        if schema.fail_on_duplicate_keys:
            raise NormalizeError(f"{len(qr.duplicate_keys)} colliding keys with key_fields={schema.key_fields}")
    if qr.null_key_rows:
        raise NormalizeError(f"{qr.null_key_rows} rows have a null key for key_fields={schema.key_fields}")

    return out, qr


def key_candidates_report(rows: list[dict[str, Any]], candidates: list[str]) -> dict[str, Any]:
    """Friday helper: for each candidate field, count rows vs distinct values vs nulls."""
    total = len(rows)
    report = {}
    for c in candidates:
        vals = [norm_text(r.get(c)) for r in rows]
        non_null = [v for v in vals if v is not None]
        report[c] = {
            "rows": total,
            "non_null": len(non_null),
            "distinct": len(set(non_null)),
            "is_unique": len(non_null) == total and len(set(non_null)) == total,
        }
    return report
