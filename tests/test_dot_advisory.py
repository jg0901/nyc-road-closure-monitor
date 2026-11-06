import json
import logging
from pathlib import Path

import pytest

from src import compare
from src.adapters import dot_advisory as adv
from src.config import load_settings
from src.ingest import FetchError
from src.snapshot import load_manifest

from .conftest import FIXTURES, FakeResponse

WEEK1 = (FIXTURES / "advisory_week1.html").read_text(encoding="utf-8")
WEEK2 = (FIXTURES / "advisory_week2.html").read_text(encoding="utf-8")


class FakeHtmlSession:
    def __init__(self, html: str | None, script=None):
        self.html, self.script, self.calls = html, list(script or []), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if self.script:
            status, body = self.script.pop(0)
            return FakeResponse(status, None, text=body or "")
        return FakeResponse(200, None, text=self.html)


# ------------------------------------------------------------------ parser

def test_period_parsed():
    a = adv.parse_advisory(WEEK1)
    assert (a.period_start, a.period_end) == ("2026-10-31", "2026-11-06")
    assert a.sections == ["Manhattan", "Bronx", "Staten Island"]


def test_skips_banner_and_resources_before_period_heading():
    a = adv.parse_advisory(WEEK1)
    names = [r["fields"]["name"] for r in a.records]
    assert "Additional Resources" not in names
    assert not any("Marathon" in n for n in names)


def test_closures_and_events_counted():
    a = adv.parse_advisory(WEEK1)
    kinds = [r["fields"]["kind"] for r in a.records]
    assert kinds.count("closure") == 6 and kinds.count("event") == 2
    assert not a.parse_warnings


def test_closure_fields_and_multi_paragraph_description():
    a = adv.parse_advisory(WEEK1)
    r = next(x for x in a.records if x["fields"]["name"].startswith("Southbound Route 9A"))
    f = r["fields"]
    assert f["section"] == "Manhattan" and f["section_id"] == "manhattan" and f["category"] is None
    assert f["description"].startswith("One out of three") and "original configuration" in f["description"]
    assert f["updated"] is False and f["streets"] == {}


def test_updated_flag_from_fully_bold_paragraph():
    a = adv.parse_advisory(WEEK1)
    hutch = next(x for x in a.records if x["fields"]["name"].startswith("Hutchinson"))
    bruck = next(x for x in a.records if x["fields"]["name"].startswith("Bruckner"))
    assert hutch["fields"]["updated"] is True
    assert bruck["fields"]["updated"] is False


def test_event_embargo_and_labelled_street_lists():
    a = adv.parse_advisory(WEEK1)
    e = next(x for x in a.records if x["fields"]["name"].startswith("The 53rd Annual"))
    f = e["fields"]
    assert f["kind"] == "event" and f["category"] == "Festivals, Parades and Events"
    assert f["embargo"].startswith("Embargo : Special Event Construction Embargo") or \
        f["embargo"].startswith("Embargo: Special Event Construction Embargo")
    assert set(f["streets"]) == {"Formation", "Route", "Dispersal"}
    assert f["streets"]["Formation"][0] == "6th Avenue between Spring Street and Canal Street"
    abbott = next(x for x in a.records if "Abbott" in x["fields"]["name"])
    assert abbott["fields"]["streets"] == {"Location(s)": [
        "East 47th Street between 1st Avenue and 2nd Avenue",
        "1st Avenue between East 50th Street and East 42nd Street",
        "42nd Street between 1st Avenue and 6th Avenue"]}


def test_key_is_stable_across_weeks_and_hash_tracks_content():
    a1 = {r["fields"]["name"]: r for r in adv.parse_advisory(WEEK1).records}
    a2 = {r["fields"]["name"]: r for r in adv.parse_advisory(WEEK2).records}
    assert a1["Battery Park Underpass"]["key"] == a2["Battery Park Underpass"]["key"]
    assert a1["Battery Park Underpass"]["content_hash"] != a2["Battery Park Underpass"]["content_hash"]
    fr = "Front Street between Wave Street and Hannah Street"
    assert a1[fr]["content_hash"] == a2[fr]["content_hash"]


def test_week_over_week_diff():
    d = compare.compare(adv.parse_advisory(WEEK1).records, adv.parse_advisory(WEEK2).records)
    assert d.counts == {"added": 1, "modified": 1, "unchanged": 5, "missing": 2}
    assert d.added[0]["fields"]["name"].startswith("Northern Boulevard")
    assert {m["fields"]["name"] for m in d.missing} == {"Bruckner Expressway at Hunts Point Avenue",
                                                        "The 53rd Annual New Yorks Village Halloween Parade"}
    assert list(d.modified[0]["changes"]) == ["description"]


def test_layout_change_raises_parse_error():
    with pytest.raises(adv.ParseError):
        adv.parse_advisory("<html><body><div id='_content'><p>nothing here</p></div></body></html>")
    with pytest.raises(adv.ParseError):
        adv.parse_advisory("<html><body><div id='other'></div></body></html>")


def test_period_regex_tolerates_missing_weekday_and_commas():
    assert adv.parse_period("Weekly Traffic Advisory for October 31 2026 to November 6, 2026") == \
        ("2026-10-31", "2026-11-06")
    assert adv.parse_period("Weekly Traffic Advisory") == (None, None)


# ------------------------------------------------------------------ end to end

def _settings(tmp_path: Path):
    cfg_src = Path(__file__).parent.parent / "config" / "settings.example.json"
    raw = json.loads(cfg_src.read_text(encoding="utf-8"))
    raw["advisory"]["backoff_seconds"] = 0
    raw["advisory"]["max_retries"] = 2
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps(raw), encoding="utf-8")
    return load_settings(cfg, root=tmp_path)


def _reset_logging():
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h); h.close()


def _run(s, session, run_id, **kw):
    _reset_logging()
    return adv.run(s, session=session, run_id=run_id, **kw)


def test_two_weeks_end_to_end(tmp_path):
    s = _settings(tmp_path)
    assert _run(s, FakeHtmlSession(WEEK1), "w1") == 0
    assert _run(s, FakeHtmlSession(WEEK1), "w1b") == 0        # same page again → dedup
    assert _run(s, FakeHtmlSession(WEEK2), "w2") == 0
    m = load_manifest(s.advisory.manifest)
    assert [x["run_id"] for x in m["snapshots"]] == ["w1", "w2"]
    assert m["runs"][1]["deduplicated_to"] == "w1"
    assert m["snapshots"][0]["raw_path"].endswith("w1.advisory.html.gz")
    rep = json.loads((s.advisory.report_dir / "w2.report.json").read_text())
    assert rep["status"] == "success" and rep["compared_to_run_id"] == "w1"
    assert rep["counts"]["diff"] == {"added": 1, "modified": 1, "unchanged": 5, "missing": 2}
    assert rep["quality"]["by_kind"] == {"closure": 6, "event": 1}
    assert (s.advisory.raw_dir / "w1.retrieval.json").exists()
    # advisory history is separate from the API history
    assert not s.paths.manifest.exists()


def test_failed_fetch_writes_failure_report(tmp_path):
    s = _settings(tmp_path)
    assert _run(s, FakeHtmlSession(None, script=[(503, ""), (503, "")]), "w1") == 1
    m = load_manifest(s.advisory.manifest)
    assert m["snapshots"] == [] and m["runs"][0]["status"] == "failure"
    assert "FAILURE" in (s.advisory.report_dir / "latest.report.md").read_text()


def test_tiny_body_is_a_fetch_error(tmp_path):
    s = _settings(tmp_path)
    assert _run(s, FakeHtmlSession("<html></html>"), "w1") == 1
    assert "FetchError" in load_manifest(s.advisory.manifest)["runs"][0]["error"]


def test_layout_change_is_a_failure_not_an_empty_snapshot(tmp_path):
    s = _settings(tmp_path)
    assert _run(s, FakeHtmlSession(WEEK1), "w1") == 0
    broken = "<html><body><div id='_content'>" + "<p>filler</p>" * 100 + "</div></body></html>"
    assert _run(s, FakeHtmlSession(broken), "w2") == 1
    m = load_manifest(s.advisory.manifest)
    assert len(m["snapshots"]) == 1 and "ParseError" in m["runs"][-1]["error"]


def test_replay_from_saved_html(tmp_path):
    s = _settings(tmp_path)
    assert _run(s, FakeHtmlSession(WEEK1), "w1") == 0
    _reset_logging()
    rc = adv.run(s, from_html=s.advisory.raw_dir / "w1.advisory.html.gz", run_id="replay")
    assert rc == 0
    assert load_manifest(s.advisory.manifest)["runs"][-1]["deduplicated_to"] == "w1"
