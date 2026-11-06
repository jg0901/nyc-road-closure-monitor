# nyc-road-closure-monitor

Auditable collector for the NYC Open Data **Street Closures due to Construction Activities by Block** dataset.
Fetches the full dataset on a schedule, keeps a durable snapshot history, and reports what changed between pulls.

**Status:** V1 scaffold (local ingestion + snapshot diff). V2 (scheduled, observable) pending.

## Problem

The dataset is overwritten weekly by the city. There is no public history. If you want to know *when* a closure
permit appeared, changed, or disappeared, you have to collect it yourself — reproducibly, with a stable key and a
deterministic change detection, so a re-run on unchanged data never produces false changes.

## Design

```
NYC Open Data (Socrata SODA API)
        │  paginated GET, $order=:id, retries + backoff, X-App-Token
        ▼
  src/ingest.py      raw response + retrieval metadata  ──►  data/raw/<run_id>.raw.json.gz      (Bronze)
        ▼
  src/normalize.py   trim/upper text, parse dates, key check, sha256 content_hash over business fields
        ▼
  src/snapshot.py    data/snapshots/<run_id>.snapshot.json.gz + manifest.json                    (Silver)
        │            (idempotent: identical dataset → run logged, no new snapshot)
        ▼
  src/compare.py     previous vs current → added / modified (field diff) / unchanged / missing
        ▼
  src/report.py      data/reports/<run_id>.report.{json,md}  +  latest.report.{json,md}
```

Three timestamps are kept separate and never conflated:

| time | field | meaning |
|---|---|---|
| event | `work_start_date`, `work_end_date` | permit validity window — **not** proof of continuous closure |
| observation | `fetched_at_utc` | when *we* pulled the data |
| source | `source_rows_updated_at_utc` | when the *city* last updated the dataset (from Socrata metadata) |

A row that is `missing` in the latest snapshot means "not present in this pull", **not** "the road reopened".

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/settings.example.json config/settings.json
cp .env.example .env                                   # add a free Socrata app token (optional but recommended)
export SOCRATA_APP_TOKEN=...                           # or load .env with your shell / direnv
```

## Run

```bash
# full pull → snapshot → diff → report
python -m src.run --config config/settings.json

# replay a saved raw file (no network) — reproducibility check
python -m src.run --config config/settings.json --from-raw data/raw/<run_id>.raw.json.gz

# Friday key audit: is uniqueid actually unique? what about segmentid? the pair?
python -m src.run --config config/settings.json --key-check uniqueid segmentid

# tests (fixtures only, no network)
python -m pytest
```

Exit code `0` = success, `1` = failure. A failure always writes `data/reports/<run_id>.report.json` with
`"status": "failure"` and the error; it never writes a snapshot.

## Configuration (`config/settings.json`)

| key | purpose |
|---|---|
| `source.dataset_id` | Socrata 4x4 id. `i6b5-j7bu` = by Block. Swap here if the city retires it for the newer Block+Intersection dataset. |
| `source.order_by` | `:id` — stable pagination. Do not remove. |
| `source.window` | optional permit-date window. Default `2026-11-01` → `2027-01-31`: only rows whose `[work_start_date, work_end_date]` **overlaps** that range are fetched (SODA `$where`). Set to `null` for a full pull. The filter is recorded in every snapshot's metadata. |
| `schema.key_fields` | stable key. **`["uniqueid"]` is a guess from the roadmap — verify with `--key-check` on the first pull.** |
| `schema.business_fields` | fields that go into `content_hash`. Changing this list changes every hash (expected). |
| `schema.required_fields` | reported if blank; not fatal. |
| `schema.fail_on_duplicate_keys` | `false` by default so the first pull can *show* you duplicates instead of dying. |
| `quality.min_rows` | fewer rows than this = failed pull, not an empty dataset. |
| `quality.max_row_delta_pct` | warn if row count swings more than this vs the last snapshot. |

## Quality checks

Run on every pull, reported in `report.json → quality` and the Markdown report:

- key uniqueness (`duplicate_keys`), null keys (fatal)
- required fields present
- date parsing (`unparseable_dates`)
- schema drift: fields in the data not in config (`unexpected_fields`) and configured fields absent from the data (`absent_fields`)
- volume: blank pull (fatal), large row-count swing (warning)

## Idempotency

`snapshot_hash` = sha256 over the sorted `(key, content_hash)` pairs of the whole dataset.
If it equals the latest snapshot's hash, the run is logged in `manifest.json → runs` with `deduplicated_to`,
and no new snapshot file or entry is written. Running twice on unchanged data → 1 snapshot, 2 runs.

## Secondary source: NYC DOT Weekly Traffic Advisory (scraper)

`src/adapters/dot_advisory.py` scrapes https://www.nyc.gov/html/dot/html/motorist/weektraf.shtml.
It is a **separate adapter with separate history** (`data/advisory/`): temporary/event closures, which the page
itself says exclude emergency situations and long-term construction. Never merge its rows with the API dataset.

```bash
python -m src.adapters.dot_advisory --config config/settings.json              # fetch → raw html.gz → parse → snapshot → diff → report
python -m src.adapters.dot_advisory --config config/settings.json --from-html data/advisory/raw/<run_id>.advisory.html.gz
```

Page model (verified 2026-11-06; `div#_content` is a flat sequence):
`<h2>Weekly Traffic Advisory for <start> to <end>` → per-borough `<h2 id=...>` → bare `<strong>location</strong>` + `<p>` description
(a fully-bold `<p>` = DOT's "updated" marker) → `<h3>Festivals, Parades and Events` → `<h4>event` + `Embargo` paragraph +
`<strong>Location(s):/Formation:/Route:/Dispersal:</strong>` + `<ul>` street lists.

Records: `kind=closure|event`, `section`, `name`, `description`, `updated`, `embargo`, `streets{label: [..]}`.
Key = sha1(kind|section|name) — there is no ID on the page, so a reworded location shows as *missing + added*.
A layout change raises `ParseError` → failure report, no snapshot. The raw HTML of every pull is kept, so the
parser can be fixed and replayed with `--from-html`.

## Limitations

- Permit windows ≠ continuous closure. Per NYC DOT, a full-closure permit covers a period during which the street
  *may* be closed only part of the time; the actual closed hours are only on the permit itself.
- No history exists before collection starts. The window filters **event** time (permit dates), not observation
  time: the first snapshot is whatever the city publishes on the day of the first run. A permit that was listed
  on Nov 1 and removed before the first run is invisible to this collector.
- `segmentid` can describe road geometry, not a unique permit — do not use it alone as a key.
- This dataset (construction permits) and the DOT Weekly Traffic Advisory (temporary/event closures) are not
  interchangeable, which is why the advisory scraper keeps its own history.
- Key/business field names in `settings.example.json` are unverified until the first live pull.
- Advisory scraper: no stable IDs on the page; keys are derived from wording. The page is replaced weekly, so
  week-over-week diffs mostly reflect the new advisory period, not changes to a persistent record.

## Project layout

```
src/ingest.py       API pagination, retries, timeout, response metadata
src/normalize.py    type conversions, key checks, canonical row fingerprint
src/snapshot.py     durable snapshots + manifest, idempotency
src/compare.py      snapshot diff → business change categories
src/report.py       compact JSON + Markdown run report
src/run.py          single-command orchestrator / CLI
src/adapters/       dot_advisory.py — Weekly Traffic Advisory HTML scraper (separate history)
tests/              synthetic cases: first run, unchanged, modified, missing, bad response, blank pull, advisory parse/diff
data/sample/        small publishable example only (history lives outside git)
docs/               notes, V2 workflow draft
```

## Sample output

_(fill after first real run: paste `data/reports/latest.report.md` and a trimmed `manifest.json`)_

## V2 (pending)

- [ ] scheduled run (GitHub Actions — see `docs/v2-workflow-draft.yml`) with history persisted outside the runner
- [ ] two successful scheduled runs observed
- [ ] failure demonstration (forced bad dataset id → failure report, non-zero exit)
- [ ] optional: POST `latest.report.json` to an n8n webhook

## Sources

- NYC Open Data — Street Closures due to Construction Activities by Block: https://data.cityofnewyork.us/d/i6b5-j7bu
- Socrata SODA API docs: https://dev.socrata.com/docs/endpoints.html
- NYC DOT Street Works Manual (permit semantics): http://streetworksmanual.nyc
