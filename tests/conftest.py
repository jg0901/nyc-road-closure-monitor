"""Shared fixtures: synthetic rows, a fake HTTP session, and a temp project tree."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.config import load_settings

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text or (json.dumps(body) if body is not None else "")

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


class FakeSession:
    """Serves paginated rows for /resource/ and metadata for /api/views/.

    `script` lets a test inject failures: a list of (status, body) consumed
    in order before falling back to normal paging.
    """

    def __init__(self, rows: list[dict], page_size: int = 1000, metadata: dict | None = None,
                 script: list[tuple[int, Any]] | None = None):
        self.rows = rows
        self.page_size = page_size
        self.metadata = metadata if metadata is not None else {
            "name": "Street Closures (fixture)", "rowsUpdatedAt": 1762300800,
            "columns": [{"fieldName": "uniqueid"}, {"fieldName": "segmentid"}]}
        self.script = list(script or [])
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.script:
            status, body = self.script.pop(0)
            return FakeResponse(status, body, text=str(body))
        if "/api/views/" in url:
            return FakeResponse(200, self.metadata)
        offset = int(params.get("$offset", 0))
        limit = int(params.get("$limit", self.page_size))
        return FakeResponse(200, self.rows[offset: offset + limit])


@pytest.fixture
def base_rows() -> list[dict]:
    return load_fixture("rows_base.json")


@pytest.fixture
def modified_rows() -> list[dict]:
    return load_fixture("rows_modified.json")


@pytest.fixture
def project(tmp_path: Path):
    """Temp project root with the example config copied in; returns (root, settings)."""
    cfg_src = Path(__file__).parent.parent / "config" / "settings.example.json"
    cfg = tmp_path / "config" / "settings.json"
    cfg.parent.mkdir(parents=True)
    raw = json.loads(cfg_src.read_text(encoding="utf-8"))
    raw["source"]["page_size"] = 2          # force multi-page paging in tests
    raw["source"]["backoff_seconds"] = 0    # no sleeping in tests
    raw["source"]["max_retries"] = 3
    cfg.write_text(json.dumps(raw), encoding="utf-8")
    settings = load_settings(cfg, root=tmp_path)
    return tmp_path, settings
