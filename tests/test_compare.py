from src import compare, normalize
from src.config import SchemaConfig

SCHEMA = SchemaConfig(
    key_fields=["uniqueid"],
    business_fields=["segmentid", "onstreetname", "fromstreetname", "tostreetname",
                     "borough", "work_start_date", "work_end_date", "purpose"],
    date_fields=["work_start_date", "work_end_date"],
)


def _norm(rows):
    return normalize.normalize_rows(rows, SCHEMA)[0]


def test_first_run_everything_added(base_rows):
    d = compare.compare(None, _norm(base_rows))
    assert d.counts == {"added": 5, "modified": 0, "unchanged": 0, "missing": 0}
    assert d.has_changes


def test_unchanged_rerun(base_rows):
    d = compare.compare(_norm(base_rows), _norm(base_rows))
    assert d.counts == {"added": 0, "modified": 0, "unchanged": 5, "missing": 0}
    assert not d.has_changes


def test_added_modified_missing(base_rows, modified_rows):
    d = compare.compare(_norm(base_rows), _norm(modified_rows))
    assert d.counts == {"added": 1, "modified": 2, "unchanged": 2, "missing": 1}
    assert d.added[0]["key"] == "1006"
    assert d.missing[0]["key"] == "1005"
    mods = {m["key"]: m["changes"] for m in d.modified}
    # 1001: only the end date changed; case/whitespace change on onstreetname is NOT a change
    assert mods["1001"] == {"work_end_date": {"from": "2026-11-20", "to": "2026-12-05"}}
    assert mods["1003"] == {"purpose": {"from": "MILLING", "to": "RESURFACING"}}
    assert d.field_change_counts == {"work_end_date": 1, "purpose": 1}


def test_diff_is_symmetric_in_direction(base_rows, modified_rows):
    fwd = compare.compare(_norm(base_rows), _norm(modified_rows))
    back = compare.compare(_norm(modified_rows), _norm(base_rows))
    assert fwd.counts["added"] == back.counts["missing"]
    assert fwd.counts["missing"] == back.counts["added"]
    assert fwd.counts["modified"] == back.counts["modified"]
