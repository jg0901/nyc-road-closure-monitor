"""End-to-end through src.run with a fake session: first run, unchanged rerun,
modified run, failed fetch, blank pull."""
import json
import logging

from src import run as runner
from src.snapshot import load_manifest

from .conftest import FakeSession


def _reset_logging():
    # basicConfig is a no-op once handlers exist; clear so each run logs to its own file.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()


def _run(settings, session, run_id):
    _reset_logging()
    return runner.run(settings, session=session, run_id=run_id)


def _report(settings, run_id):
    return json.loads((settings.paths.report_dir / f"{run_id}.report.json").read_text())


def test_first_run_creates_snapshot_and_report(project, base_rows):
    root, s = project
    assert _run(s, FakeSession(base_rows, page_size=2), "r1") == 0
    m = load_manifest(s.paths.manifest)
    assert len(m["snapshots"]) == 1 and m["runs"][0]["status"] == "success"
    rep = _report(s, "r1")
    assert rep["status"] == "success"
    assert rep["counts"]["diff"] == {"added": 5, "modified": 0, "unchanged": 0, "missing": 0}
    assert rep["source"]["fetched_at_utc"] and rep["source"]["source_rows_updated_at_utc"]
    assert (s.paths.raw_dir / "r1.raw.json.gz").exists()
    assert (s.paths.report_dir / "latest.report.md").exists()
    assert (s.paths.log_dir / "r1.log").exists()


def test_rerun_unchanged_is_idempotent(project, base_rows):
    root, s = project
    assert _run(s, FakeSession(base_rows, page_size=2), "r1") == 0
    assert _run(s, FakeSession(base_rows, page_size=2), "r2") == 0
    m = load_manifest(s.paths.manifest)
    assert len(m["snapshots"]) == 1            # no duplicate snapshot entry
    assert len(m["runs"]) == 2
    assert m["runs"][1]["deduplicated_to"] == "r1"
    assert not (s.paths.snapshot_dir / "r2.snapshot.json.gz").exists()
    rep = _report(s, "r2")
    assert rep["counts"]["diff"] == {"added": 0, "modified": 0, "unchanged": 5, "missing": 0}


def test_modified_run_diffs_against_previous(project, base_rows, modified_rows):
    root, s = project
    assert _run(s, FakeSession(base_rows, page_size=2), "r1") == 0
    assert _run(s, FakeSession(modified_rows, page_size=2), "r2") == 0
    m = load_manifest(s.paths.manifest)
    assert len(m["snapshots"]) == 2
    rep = _report(s, "r2")
    assert rep["compared_to_run_id"] == "r1"
    assert rep["counts"]["diff"] == {"added": 1, "modified": 2, "unchanged": 2, "missing": 1}
    assert rep["field_change_counts"] == {"work_end_date": 1, "purpose": 1}
    md = (s.paths.report_dir / "r2.report.md").read_text()
    assert "| 1 | 2 | 2 | 1 |" in md and "1006" in md and "1005" in md


def test_failed_fetch_is_not_an_empty_dataset(project, base_rows):
    root, s = project
    assert _run(s, FakeSession(base_rows, page_size=2), "r1") == 0
    dead = FakeSession(base_rows, script=[(503, None)] * 3)
    assert _run(s, dead, "r2") == 1
    m = load_manifest(s.paths.manifest)
    assert len(m["snapshots"]) == 1                      # nothing written
    assert m["runs"][-1]["status"] == "failure"
    rep = _report(s, "r2")
    assert rep["status"] == "failure" and "fetch failed" in rep["error"]
    assert rep["counts"]["diff"] is None                 # no fake "5 missing"
    assert "FAILURE" in (s.paths.report_dir / "latest.report.md").read_text()


def test_blank_pull_is_a_failure(project):
    root, s = project
    assert _run(s, FakeSession([], page_size=2), "r1") == 1
    rep = _report(s, "r1")
    assert rep["status"] == "failure" and "0 rows" in rep["error"]
    assert load_manifest(s.paths.manifest)["snapshots"] == []


def test_volume_swing_is_warned_not_fatal(project, base_rows):
    root, s = project
    assert _run(s, FakeSession(base_rows, page_size=2), "r1") == 0
    assert _run(s, FakeSession(base_rows[:1], page_size=2), "r2") == 0
    rep = _report(s, "r2")
    assert rep["status"] == "success"
    assert any("row count moved" in w for w in rep["warnings"])


def test_replay_from_raw_needs_no_network(project, base_rows):
    root, s = project
    assert _run(s, FakeSession(base_rows, page_size=2), "r1") == 0
    _reset_logging()
    rc = runner.run(s, session=None, from_raw=s.paths.raw_dir / "r1.raw.json.gz", run_id="replay")
    assert rc == 0
    assert load_manifest(s.paths.manifest)["runs"][-1]["deduplicated_to"] == "r1"
