from src import normalize
from src.config import SchemaConfig

from .conftest import load_fixture


def _schema(**over) -> SchemaConfig:
    base = dict(key_fields=["uniqueid"],
                business_fields=["segmentid", "onstreetname", "fromstreetname", "tostreetname",
                                 "borough", "work_start_date", "work_end_date", "purpose"],
                date_fields=["work_start_date", "work_end_date"],
                required_fields=["uniqueid", "segmentid", "work_start_date", "work_end_date"])
    base.update(over)
    return SchemaConfig(**base)


def test_text_and_date_normalization(base_rows):
    out, qr = normalize.normalize_rows(base_rows, _schema())
    assert qr.ok and qr.row_count == 5 and len(out) == 5
    r1 = next(r for r in out if r["key"] == "1001")
    assert r1["fields"]["onstreetname"] == "BROADWAY"          # trimmed + upper
    assert r1["original"]["onstreetname"] == "  broadway "     # original preserved
    assert r1["fields"]["work_start_date"] == "2026-11-03"     # ISO floating timestamp
    r3 = next(r for r in out if r["key"] == "1003")
    assert r3["fields"]["work_start_date"] == "2026-11-01"     # MM/DD/YYYY
    assert r1["system"][":id"] == "row-1"


def test_hash_is_deterministic_and_ignores_system_fields(base_rows):
    a, _ = normalize.normalize_rows(base_rows, _schema())
    rows2 = [dict(r, **{":updated_at": "2099-01-01T00:00:00.000"}) for r in base_rows]
    b, _ = normalize.normalize_rows(rows2, _schema())
    assert [r["content_hash"] for r in a] == [r["content_hash"] for r in b]


def test_hash_ignores_whitespace_and_case(base_rows):
    a, _ = normalize.normalize_rows(base_rows, _schema())
    rows2 = [dict(r, onstreetname=r["onstreetname"].strip().lower()) for r in base_rows]
    b, _ = normalize.normalize_rows(rows2, _schema())
    assert [r["content_hash"] for r in a] == [r["content_hash"] for r in b]


def test_hash_changes_on_business_field_change(base_rows):
    a, _ = normalize.normalize_rows(base_rows, _schema())
    rows2 = [dict(r) for r in base_rows]
    rows2[0]["work_end_date"] = "2027-01-01T00:00:00.000"
    b, _ = normalize.normalize_rows(rows2, _schema())
    assert a[0]["content_hash"] != b[0]["content_hash"]
    assert a[1]["content_hash"] == b[1]["content_hash"]


def test_quality_report_flags_dups_missing_bad_dates():
    rows = load_fixture("rows_bad_quality.json")
    out, qr = normalize.normalize_rows(rows, _schema())
    assert not qr.ok
    assert qr.duplicate_keys == {"1001": 2}
    assert qr.missing_required == {"segmentid": 1}
    assert qr.unparseable_dates == {"work_end_date": 1}
    assert len(out) == 3  # not fatal by default


def test_duplicate_keys_can_be_fatal():
    rows = load_fixture("rows_bad_quality.json")
    try:
        normalize.normalize_rows(rows, _schema(fail_on_duplicate_keys=True))
        assert False, "expected NormalizeError"
    except normalize.NormalizeError:
        pass


def test_null_key_is_fatal(base_rows):
    rows = [dict(r) for r in base_rows]
    rows[0]["uniqueid"] = ""
    try:
        normalize.normalize_rows(rows, _schema())
        assert False, "expected NormalizeError"
    except normalize.NormalizeError:
        pass


def test_schema_drift_detection(base_rows):
    rows = [dict(r, brand_new_col="x") for r in base_rows]
    _, qr = normalize.normalize_rows(rows, _schema(business_fields=["segmentid", "not_in_data"]))
    assert "brand_new_col" in qr.unexpected_fields
    assert qr.absent_fields == ["not_in_data"]


def test_key_candidates_report(base_rows):
    rep = normalize.key_candidates_report(base_rows, ["uniqueid", "borough"])
    assert rep["uniqueid"]["is_unique"] is True
    assert rep["borough"]["is_unique"] is True   # 5 distinct boroughs in fixture
    rep2 = normalize.key_candidates_report(load_fixture("rows_bad_quality.json"), ["uniqueid"])
    assert rep2["uniqueid"]["is_unique"] is False
