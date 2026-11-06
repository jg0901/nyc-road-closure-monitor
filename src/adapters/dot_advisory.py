"""Adapter: NYC DOT Weekly Traffic Advisory (HTML scrape).

Separate source from the Open Data construction-closure API, with its own raw/snapshot/report
history. Covers temporary/event closures; per the page itself it does NOT reflect emergency
closures or long-term construction projects.

Page structure (verified 2026-11-06; logically a FLAT sequence under div#_content, but the
parser walks descendants because some html.parser builds nest everything after a <br /> inside it):

    <h2>Weekly Traffic Advisory for Saturday October 31, 2026, to Friday November 6, 2026</h2>
    <p>intro</p> <p><strong>*Note: Bold text indicates updated information.</strong></p> <ul>jump links</ul>
    <h2 id="manhattan">Manhattan</h2>
      <strong>Battery Park Underpass</strong>            <- bare <strong>, direct child = closure location
      <p>As of Sunday October 25th, 2026, ... </p>       <- description (may be several <p>)
      <p><strong>whole paragraph bold</strong></p>       <- "updated" marker
      <h3>Festivals, Parades and Events</h3>
      <h4>2026 Abbott Dash To The Finish Line 5K</h4>    <- event
      <p>The following streets will be closed ...</p>
      <p><strong>Embargo</strong>: Special Event Construction Embargo / <strong>Oct 22 ... </strong></p>
      <strong>Location(s):</strong> <ul><li>street</li>...</ul>   <- labelled street lists
      <strong>Formation:</strong> / <strong>Route:</strong> / <strong>Dispersal:</strong> + <ul>
    <h2 id="bronx">Bronx</h2> ...

Pages: weekly (weektraf.shtml) and weekend (wkndtraf.shtml) share this grammar; the weekend page
wraps its sections in an extra <div>. Each page keeps its own history under data/advisory/<page>/.

Records:
  kind=closure : key = sha1(<page>|closure|<section>|<location>)
  kind=event   : key = sha1(<page>|event|<section>|<event name>)
Keys are stable across weeks as long as DOT keeps the wording; a reworded location shows up as
missing + added, which is honest — there is no ID on the page.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from .. import compare, report, snapshot
from ..config import AdvisoryConfig, Settings, load_settings
from ..ingest import FetchError, _get_with_retries
from ..normalize import content_hash, norm_text

log = logging.getLogger("nyc_road_closure_monitor.advisory")

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_HEADING_RE = re.compile(r"^(weekly|weekend)\s+traffic\s+advisory\s+for\b", re.I)
_PERIOD_RE = re.compile(
    rf"(?:Weekly|Weekend) Traffic Advisory for\s+(?:\w+\s+)?({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}}),?\s+to\s+(?:\w+\s+)?({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})",
    re.I)
_STRUCTURAL = {"h2", "h3", "h4", "strong", "p", "ul"}


class ParseError(ValueError):
    """Page fetched but its structure did not match what the parser expects."""


@dataclass
class Advisory:
    period_start: str | None
    period_end: str | None
    period_text: str | None
    records: list[dict[str, Any]] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)


def _txt(el: Tag) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _structural_elements(root: Tag):
    """Yield h2/h3/h4/strong/p/ul in document order, skipping any that sit inside another
    structural tag (strong inside p, li inside ul). Does NOT require them to be direct children:
    some parser builds (seen: Python 3.13 + bs4 4.14) nest everything after a `<br />` inside the
    br, and DOT may wrap sections in divs. Walking descendants makes both irrelevant."""
    for el in root.descendants:
        if not isinstance(el, Tag) or el.name not in _STRUCTURAL:
            continue
        parent, nested = el.parent, False
        while parent is not None and parent is not root:
            if parent.name in _STRUCTURAL:
                nested = True
                break
            parent = parent.parent
        if not nested:
            yield el


def _is_fully_bold(p: Tag) -> bool:
    """<p><strong>whole paragraph</strong></p> → DOT's 'updated information' marker."""
    kids = [c for c in p.children if not (isinstance(c, str) and not c.strip())]
    return len(kids) == 1 and isinstance(kids[0], Tag) and kids[0].name in ("strong", "b")


def parse_period(text: str) -> tuple[str | None, str | None]:
    m = _PERIOD_RE.search(text)
    if not m:
        return None, None
    def iso(mon: str, day: str, year: str) -> str:
        return datetime.strptime(f"{mon} {day} {year}", "%B %d %Y").date().isoformat()
    return iso(m.group(1), m.group(2), m.group(3)), iso(m.group(4), m.group(5), m.group(6))


def _key(page: str, kind: str, section: str, name: str) -> str:
    raw = f"{page}|{kind}|{norm_text(section)}|{norm_text(name)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _finish(rec: dict[str, Any] | None, out: list[dict[str, Any]]) -> None:
    if rec is None:
        return
    f = rec["fields"]
    f["description"] = " ".join(rec.pop("_paras")).strip() or None
    rec["content_hash"] = content_hash(f)
    out.append(rec)


def parse_advisory(html: str, content_selector: str = "#_content", page: str = "weekly") -> Advisory:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(content_selector)
    if root is None:
        raise ParseError(f"content container {content_selector!r} not found")

    adv = Advisory(None, None, None)
    started = False
    section: str | None = None
    section_id: str | None = None
    category: str | None = None
    current: dict[str, Any] | None = None
    pending_label: str | None = None

    for el in _structural_elements(root):
        tag, text = el.name, _txt(el)
        if not text and tag != "ul":
            continue

        if tag == "h2" and _HEADING_RE.match(text):
            started = True
            adv.period_text = text
            adv.period_start, adv.period_end = parse_period(text)
            if not adv.period_start:
                adv.parse_warnings.append(f"could not parse period from heading: {text!r}")
            continue
        if not started:
            continue  # skip marathon banner, "Additional Resources", etc.

        if tag == "h2":
            _finish(current, adv.records); current = None
            section, section_id, category = text, el.get("id"), None
            adv.sections.append(section)
            continue
        if section is None:
            continue  # intro paragraphs / jump-link list before the first borough

        if tag == "h3":
            _finish(current, adv.records); current = None
            category = text
            continue

        if tag == "h4":
            _finish(current, adv.records)
            current = {"key": _key(page, "event", section, text), "_paras": [],
                       "fields": {"page": page, "kind": "event", "section": section, "section_id": section_id,
                                  "category": category, "name": text, "embargo": None,
                                  "updated": False, "streets": {}},
                       "original": {"name": text}, "system": {}}
            continue

        if tag == "strong":
            if text.endswith(":"):
                # "Location(s):", "Formation:", "Route:", "Dispersal:", "Miscellaneous:" ... → label for the next <ul>
                pending_label = text.rstrip(":").strip()
                continue
            # bare <strong> = closure location heading
            _finish(current, adv.records)
            current = {"key": _key(page, "closure", section, text), "_paras": [],
                       "fields": {"page": page, "kind": "closure", "section": section, "section_id": section_id,
                                  "category": category, "name": text, "embargo": None,
                                  "updated": False, "streets": {}},
                       "original": {"name": text}, "system": {}}
            continue

        if tag == "ul":
            items = [_txt(li) for li in el.find_all("li") if _txt(li)]
            if current is not None and items:
                current["fields"]["streets"][pending_label or "Streets"] = items
            pending_label = None
            continue

        if tag == "p":
            if current is None:
                continue  # stray paragraph before any location — ignore
            if _is_fully_bold(el):
                current["fields"]["updated"] = True
            if text.lower().startswith("embargo"):
                current["fields"]["embargo"] = text
            else:
                current["_paras"].append(text)
            continue

    _finish(current, adv.records)

    if not adv.records:
        raise ParseError("no closure/event records parsed — page layout probably changed")
    dup = {}
    for r in adv.records:
        dup[r["key"]] = dup.get(r["key"], 0) + 1
    dups = [k for k, n in dup.items() if n > 1]
    if dups:
        adv.parse_warnings.append(f"{len(dups)} duplicate keys (same section + same name)")
    return adv


# ---------------------------------------------------------------- fetch / persist / run

def fetch_html(session: Any, cfg: AdvisoryConfig) -> tuple[str, dict[str, Any]]:
    request_log: list[dict[str, Any]] = []
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    html = _get_with_retries(session, cfg.url, None, cfg, request_log, expect="text")
    if not isinstance(html, str) or len(html) < 500:
        raise FetchError(f"advisory body too small ({len(html) if isinstance(html, str) else 0} bytes)")
    return html, {"source_id": cfg.source_id, "source_url": cfg.url, "fetched_at_utc": fetched_at,
                  "bytes": len(html), "request_log": request_log, "dataset_metadata": {}}


def save_raw_html(html: str, retrieval: dict[str, Any], raw_dir: Path, run_id: str) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    out = raw_dir / f"{run_id}.advisory.html.gz"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        f.write(html)
    (raw_dir / f"{run_id}.retrieval.json").write_text(json.dumps(retrieval, indent=2), encoding="utf-8")
    return out


def _attach_log_file(log_path: Path) -> None:
    """One log file per page per run; basicConfig only works once, so manage handlers directly."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler) and getattr(h, "_advisory", False):
            root.removeHandler(h); h.close()
    fh = logging.FileHandler(log_path, encoding="utf-8"); fh._advisory = True  # type: ignore[attr-defined]
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        sh = logging.StreamHandler(sys.stderr); sh.setFormatter(fh.formatter); root.addHandler(sh)


def run(settings: Settings, session: Any = None, from_html: Path | None = None,
        run_id: str | None = None, pages: list[str] | None = None) -> int:
    """Run every configured advisory page (or just `pages`). Exit 1 if any page failed."""
    cfgs = [c for c in settings.advisory if not pages or c.name in pages]
    if not cfgs:
        print("no advisory pages configured/selected", file=sys.stderr)
        return 1
    if from_html and len(cfgs) != 1:
        print("--from-html needs exactly one page: pass --page weekly|weekend", file=sys.stderr)
        return 1
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rc = 0
    for cfg in cfgs:
        rc = max(rc, run_page(cfg, session=session, from_html=from_html, run_id=run_id))
    return rc


def run_page(cfg: AdvisoryConfig, session: Any = None, from_html: Path | None = None,
             run_id: str | None = None) -> int:
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = cfg.log_dir / f"{run_id}.log"
    _attach_log_file(log_path)
    log.info("advisory[%s] run %s start", cfg.name, run_id)
    raw_path: Path | None = None

    try:
        if from_html:
            html = from_html.read_text(encoding="utf-8") if from_html.suffix != ".gz" else \
                gzip.open(from_html, "rt", encoding="utf-8").read()
            retrieval = {"source_id": cfg.source_id, "source_url": str(from_html),
                         "fetched_at_utc": None, "bytes": len(html), "dataset_metadata": {}}
            raw_path = from_html
        else:
            if session is None:
                import requests
                session = requests.Session()
            html, retrieval = fetch_html(session, cfg)
            raw_path = save_raw_html(html, retrieval, cfg.raw_dir, run_id)
        adv = parse_advisory(html, cfg.content_selector, page=cfg.name)
    except (FetchError, ParseError) as e:
        return _fail(cfg, run_id, f"{type(e).__name__}: {e}", log_path)

    retrieval["dataset_metadata"] = {"name": f"NYC DOT {cfg.name.title()} Traffic Advisory", "page": cfg.name,
                                     "rows_updated_at_utc": None,
                                     "advisory_period_start": adv.period_start,
                                     "advisory_period_end": adv.period_end,
                                     "advisory_period_text": adv.period_text,
                                     "sections": adv.sections}
    retrieval["row_count"] = len(adv.records)
    retrieval["pages"] = 1
    kinds = {}
    for r in adv.records:
        kinds[r["fields"]["kind"]] = kinds.get(r["fields"]["kind"], 0) + 1
    quality = {"row_count": len(adv.records), "ok": not adv.parse_warnings, "by_kind": kinds,
               "sections": len(adv.sections), "updated_flagged": sum(r["fields"]["updated"] for r in adv.records),
               "duplicate_key_count": 0, "null_key_rows": 0, "missing_required": {},
               "unparseable_dates": {}, "absent_fields": [], "parse_warnings": adv.parse_warnings}

    manifest = snapshot.load_manifest(cfg.manifest)
    prev_entry = snapshot.latest_snapshot_entry(manifest)
    run_entry = snapshot.persist(adv.records, retrieval, quality, cfg.snapshot_dir, cfg.manifest, run_id, raw_path)
    prev_rows = snapshot.load_snapshot(Path(prev_entry["path"])) if prev_entry else None
    diff = compare.compare(prev_rows, adv.records)

    rep = report.build_report(run_id, "success", retrieval=retrieval, quality=quality, diff=diff.to_dict(),
                              run_entry=run_entry, compared_to=prev_entry["run_id"] if prev_entry else None,
                              log_path=log_path, warnings=adv.parse_warnings)
    report.write_report(rep, cfg.report_dir, diff.to_dict())
    log.info("advisory[%s] run %s success: period %s..%s, %s, diff %s",
             cfg.name, run_id, adv.period_start, adv.period_end, kinds, diff.counts)
    return 0


def _fail(cfg: AdvisoryConfig, run_id: str, error: str, log_path: Path) -> int:
    log.error(error)
    manifest = snapshot.load_manifest(cfg.manifest)
    snapshot.record_run(manifest, run_id, "failure", error=error, snapshot_written=False)
    snapshot.save_manifest(cfg.manifest, manifest)
    rep = report.build_report(run_id, "failure", error=error, log_path=log_path)
    report.write_report(rep, cfg.report_dir)
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NYC DOT Weekly Traffic Advisory scraper")
    p.add_argument("--config", default="config/settings.json")
    p.add_argument("--root", default=".")
    p.add_argument("--from-html", type=Path, help="parse a saved page (.html or .html.gz) instead of fetching")
    p.add_argument("--run-id")
    p.add_argument("--page", action="append", help="only this page (weekly/weekend); repeatable")
    a = p.parse_args(argv)
    return run(load_settings(a.config, root=a.root), from_html=a.from_html, run_id=a.run_id, pages=a.page)


if __name__ == "__main__":
    sys.exit(main())
