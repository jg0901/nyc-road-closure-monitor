import pytest

from src import ingest
from src.config import SourceConfig

from .conftest import FakeSession


def _cfg(**over) -> SourceConfig:
    base = dict(source_id="t", domain="https://example.test", dataset_id="abcd-1234",
                page_size=2, max_retries=3, backoff_seconds=0)
    base.update(over)
    return SourceConfig(**base)


def test_paginates_all_rows_in_order(base_rows):
    s = FakeSession(base_rows, page_size=2)
    r = ingest.fetch_all(s, _cfg())
    assert r.row_count == 5 and r.pages == 3
    assert [x["uniqueid"] for x in r.rows] == ["1001", "1002", "1003", "1004", "1005"]
    resource_calls = [c for c in s.calls if "/resource/" in c["url"]]
    assert [c["params"]["$offset"] for c in resource_calls] == [0, 2, 4]
    assert all(c["params"]["$order"] == ":id" for c in resource_calls)
    assert all(c["params"]["$select"] == ":*,*" for c in resource_calls)


def test_exact_page_multiple_terminates(base_rows):
    s = FakeSession(base_rows[:4], page_size=2)
    r = ingest.fetch_all(s, _cfg())
    assert r.row_count == 4 and r.pages == 3  # third page empty -> stop


def test_metadata_and_retrieval_fields(base_rows):
    r = ingest.fetch_all(FakeSession(base_rows), _cfg())
    m = r.retrieval_metadata()
    assert m["source_url"].endswith("/resource/abcd-1234.json")
    assert m["fetched_at_utc"].endswith("Z")
    assert m["dataset_metadata"]["name"] == "Street Closures (fixture)"
    assert m["dataset_metadata"]["rows_updated_at_utc"] == "2025-11-05T00:00:00Z"
    assert "uniqueid" in m["dataset_metadata"]["columns"]
    assert len(m["request_log"]) == 4  # 3 pages + metadata


def test_app_token_header(base_rows, monkeypatch):
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "tok123")
    s = FakeSession(base_rows)
    ingest.fetch_all(s, _cfg())
    assert s.calls[0]["headers"]["X-App-Token"] == "tok123"


def test_retries_then_succeeds(base_rows):
    s = FakeSession(base_rows, script=[(503, None), (429, None)])
    r = ingest.fetch_all(s, _cfg())
    assert r.row_count == 5
    statuses = [c["status"] for c in r.request_log[:3]]
    assert statuses == [503, 429, 200]


def test_bad_response_raises_not_empty(base_rows):
    s = FakeSession(base_rows, script=[(503, None), (503, None), (503, None)])
    with pytest.raises(ingest.FetchError):
        ingest.fetch_all(s, _cfg())


def test_non_retryable_status_fails_fast(base_rows):
    s = FakeSession(base_rows, script=[(404, None)])
    with pytest.raises(ingest.FetchError):
        ingest.fetch_all(s, _cfg())
    assert len([c for c in s.calls if "/resource/" in c["url"]]) == 1


def test_non_array_body_raises(base_rows):
    s = FakeSession(base_rows, script=[(200, {"error": "weird"})])
    with pytest.raises(ingest.FetchError):
        ingest.fetch_all(s, _cfg())


def test_raw_roundtrip(tmp_path, base_rows):
    r = ingest.fetch_all(FakeSession(base_rows), _cfg())
    p = ingest.save_raw(r, tmp_path / "raw", "run1")
    back = ingest.load_raw(p)
    assert back["rows"] == base_rows
    assert back["retrieval"]["row_count"] == 5


def test_window_builds_overlap_where_and_records_it(base_rows):
    cfg = _cfg(window={"start": "2026-11-01", "end": "2027-01-31"})
    s = FakeSession(base_rows)
    r = ingest.fetch_all(s, cfg)
    where = s.calls[0]["params"]["$where"]
    assert where == ("work_end_date >= '2026-11-01T00:00:00.000' AND "
                     "work_start_date <= '2027-01-31T23:59:59.999'")
    assert r.retrieval_metadata()["dataset_metadata"]["window_where"] == where


def test_no_window_means_full_pull(base_rows):
    s = FakeSession(base_rows)
    r = ingest.fetch_all(s, _cfg(window=None))
    assert "$where" not in s.calls[0]["params"]
    assert r.retrieval_metadata()["dataset_metadata"]["window_where"] is None
