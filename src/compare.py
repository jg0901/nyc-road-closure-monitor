"""Compare two complete snapshots into business change categories.

Categories (per roadmap V1 definition of done):
- added     : key in current, not in previous
- modified  : key in both, content_hash differs (field-level diff included)
- unchanged : key in both, content_hash equal
- missing   : key in previous, not in current  ("not present in this snapshot",
              NOT "the road reopened")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiffResult:
    added: list[dict[str, Any]] = field(default_factory=list)
    modified: list[dict[str, Any]] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)   # keys only, to keep output compact
    missing: list[dict[str, Any]] = field(default_factory=list)
    field_change_counts: dict[str, int] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return {"added": len(self.added), "modified": len(self.modified),
                "unchanged": len(self.unchanged), "missing": len(self.missing)}

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.missing)

    def to_dict(self) -> dict[str, Any]:
        return {"counts": self.counts, "field_change_counts": self.field_change_counts,
                "added": self.added, "modified": self.modified, "missing": self.missing,
                "unchanged_keys": self.unchanged}


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["key"] in idx:
            # Duplicate key inside one snapshot: keep first, flag via normalize's QualityReport.
            continue
        idx[r["key"]] = r
    return idx


def field_diff(prev: dict[str, Any], curr: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for f in sorted(set(prev) | set(curr)):
        if prev.get(f) != curr.get(f):
            out[f] = {"from": prev.get(f), "to": curr.get(f)}
    return out


def compare(previous: list[dict[str, Any]] | None, current: list[dict[str, Any]]) -> DiffResult:
    """Diff previous -> current. previous=None means first run: everything is 'added'."""
    res = DiffResult()
    cur = _index(current)
    if previous is None:
        res.added = [{"key": k, "fields": r["fields"]} for k, r in cur.items()]
        return res

    prev = _index(previous)
    for k, r in cur.items():
        p = prev.get(k)
        if p is None:
            res.added.append({"key": k, "fields": r["fields"]})
        elif p["content_hash"] == r["content_hash"]:
            res.unchanged.append(k)
        else:
            changes = field_diff(p["fields"], r["fields"])
            res.modified.append({"key": k, "changes": changes})
            for f in changes:
                res.field_change_counts[f] = res.field_change_counts.get(f, 0) + 1
    for k, p in prev.items():
        if k not in cur:
            res.missing.append({"key": k, "fields": p["fields"]})
    return res
