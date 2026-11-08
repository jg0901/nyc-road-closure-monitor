# NYC Road Closure Monitor

**An automated, auditable pipeline for tracking changes in New York City street-closure notices over time.**

[![Collect workflow](https://github.com/jg0901/nyc-road-closure-monitor/actions/workflows/collect.yml/badge.svg)](https://github.com/jg0901/nyc-road-closure-monitor/actions/workflows/collect.yml)

**Tech stack:** Python · Socrata SODA API · Requests · Beautiful Soup · Pytest · GitHub Actions · JSON/Markdown

## Overview

NYC publishes construction-related street-closure records and traffic advisories, but its public pages and datasets do not provide a ready-to-use, longitudinal change log. A record visible today may be edited or absent on the next refresh, making it difficult to establish **what changed and when that change was observed**.

This project collects the sources on a schedule, preserves historical snapshots, compares successive observations, and generates human-readable and machine-readable reports. It is designed around **reproducibility, idempotency, data quality, and operational visibility**—not real-time traffic routing.

The monitor tracks three independent sources:

| Source | Collection method | Scope |
| --- | --- | --- |
| [NYC Open Data: Street Closures due to Construction Activities by Block](https://data.cityofnewyork.us/d/i6b5-j7bu) | Paginated Socrata API requests | Construction-related permit records, filtered by a configurable date window |
| [NYC DOT Weekly Traffic Advisory](https://www.nyc.gov/html/dot/html/motorist/weektraf.shtml) | HTML scraper | Published weekly closures and event advisories |
| [NYC DOT Weekend Traffic Advisory](https://www.nyc.gov/html/dot/html/motorist/wkndtraf.shtml) | HTML scraper | Published weekend closures and event advisories |

**Important:** These are different types of source records. The project **does not combine them into a single authoritative list of roads currently closed**.

## What the pipeline does

- **Collects reliably:** pages through the Socrata API with stable ordering, timeouts, retries, and exponential backoff; extracts advisories from NYC DOT pages.
- **Checks data quality:** detects missing keys, duplicate records, schema changes, invalid dates, empty responses, and unusual volume changes.
- **Preserves history:** stores normalized snapshots with a manifest of collection attempts and source-observation timestamps.
- **Detects changes:** labels records as `added`, `modified`, `unchanged`, or `missing`, with field-level differences for modified records.
- **Avoids duplicate snapshots:** uses deterministic content hashes so an unchanged collection creates a run record without writing a redundant snapshot.
- **Produces auditable output:** writes JSON and Markdown reports, run logs, and reproducible sample artifacts.
- **Runs automatically:** GitHub Actions executes tests and collectors daily, saves history to the repository, and exposes failures through workflow status and run artifacts.

## Architecture

```text
              NYC Open Data (Socrata API)
                           |
             Paginated fetch + retries
                           |
                  Raw JSON (local)
                           |
              Normalize + quality checks
                           |
                Versioned snapshot
                           |
               Compare with prior run
                           |
                  JSON/MD reports

    NYC DOT weekly page         NYC DOT weekend page
             |                           |
        HTML fetch                  HTML fetch
             |                           |
        Parse records               Parse records
             |                           |
      Independent history         Independent history
             |                           |
         Diff + report              Diff + report

       GitHub Actions: test -> collect -> commit history
                                  -> upload reports
                                  -> flag collector failures
```

The API collector uses `src/run.py` to orchestrate `ingest.py`, `normalize.py`, `snapshot.py`, `compare.py`, and `report.py`. The advisory collector lives in `src/adapters/dot_advisory.py` and maintains **separate snapshots, manifests, and reports for each page**.

### How changes are identified

| Category | Meaning |
| --- | --- |
| `added` | Record key exists in the current observation but not the previous snapshot. |
| `modified` | Record key exists in both, but one or more tracked business fields changed. |
| `unchanged` | Record key and tracked business-field hash match. |
| `missing` | Record key was observed previously but is absent from the current observation. |

For construction records, `uniqueid` is the configured business key. A SHA-256 hash of normalized business fields detects modifications without relying on Socrata system IDs, which may change when the source is republished. Advisory pages do not expose stable record IDs, so the scraper derives keys from page, record type, section, and name; wording changes may consequently appear as a `missing` + `added` pair.

**Interpretation matters:** `missing` means **not found in the latest collected source**, not necessarily *road reopened*. On the first run, every retained record is classified as `added` because there is no earlier snapshot for comparison.

## Verified example: November 7, 2026 (UTC)

The repository includes reports from a successful collection on **2026-11-07**:

| Source | Source records fetched / extracted | Observation |
| --- | ---: | --- |
| Construction closures API | 3,689 | 159 exact duplicate records removed; 3,530 distinct business-key records in the first snapshot |
| DOT Weekly Traffic Advisory | 21 | 21 records extracted |
| DOT Weekend Traffic Advisory | 211 | 1 duplicate-key warning; 210 distinct keys used in the comparison |

These figures are **observations from that collection**, not a count of roads physically closed at the time.

Review the committed outputs:

- [Construction collection report](data/reports/latest.report.md)
- [Weekly advisory report](data/advisory/weekly/reports/latest.report.md)
- [Weekend advisory report](data/advisory/weekend/reports/latest.report.md)
- [Construction snapshot manifest](data/snapshots/manifest.json)
- [Synthetic change-detection example](data/sample/example.report.md) — demonstrates added, modified, unchanged, and missing records across two test snapshots.

## Quick start

**Prerequisites:** Python 3.12+ and Git. An optional [Socrata app token](https://data.cityofnewyork.us/profile/app_tokens) can improve API rate limits.

```bash
git clone https://github.com/jg0901/nyc-road-closure-monitor.git
cd nyc-road-closure-monitor

python -m venv .venv
source .venv/bin/activate                # macOS/Linux
pip install -r requirements.txt
cp config/settings.example.json config/settings.json
```

On **Windows PowerShell**, activate with `.venv\Scripts\Activate.ps1` and copy the configuration with `Copy-Item config/settings.example.json config/settings.json`.

If you have a Socrata token, set it in your shell before running:

```bash
export SOCRATA_APP_TOKEN="your-token"  # macOS/Linux
```

PowerShell equivalent: `$env:SOCRATA_APP_TOKEN = "your-token"`. The `.env.example` file documents the variable, but the Python application **does not automatically load `.env`**.

### Run the collectors

```bash
# Construction permits: fetch -> validate -> snapshot -> diff -> report
python -m src.run --config config/settings.json

# Weekly and weekend DOT advisories
python -m src.adapters.dot_advisory --config config/settings.json

# Only the weekend advisory
python -m src.adapters.dot_advisory --config config/settings.json --page weekend

# Audit candidate business keys
python -m src.run --config config/settings.json --key-check uniqueid segmentid

# Replay a previously saved local API response without network access
python -m src.run --config config/settings.json --from-raw data/raw/<run_id>.raw.json.gz

# Run fixture-based automated tests
python -m pytest
```

A successful collector returns exit code `0`; a failure returns `1` and writes a failure report rather than a new snapshot. Some **nonfatal quality issues remain warnings** and do not automatically fail a run; see [Data-quality rules](#data-quality-rules).

## Configuration

Edit the local, Git-ignored `config/settings.json` file. The default example config queries construction permits whose **permit-date intervals overlap 2026-11-01 through 2027-01-31**. This is a filtered collection, **not an all-time pull**. Set `source.window` to `null` to fetch all available API records.

| Setting | Purpose |
| --- | --- |
| `source.dataset_id` | Socrata dataset identifier (`i6b5-j7bu` by default). |
| `source.page_size`, `source.order_by` | Pagination size and deterministic ordering. |
| `source.window` | Optional overlap filter on permit start/end dates. |
| `schema.key_fields` | Fields defining a record's stable business key. |
| `schema.business_fields` | Fields included in each record's content hash. |
| `schema.fail_on_duplicate_keys` | Whether conflicting duplicate business keys should abort normalization. |
| `quality.min_rows` | Minimum acceptable source row count; below this is a failed pull. |
| `quality.max_row_delta_pct` | Threshold for a row-count change warning. |
| `advisory.pages` | Configured DOT advisory pages. |
| `paths` | Output locations for API raw files, snapshots, manifests, reports, and logs. |

Changing tracked business fields changes content hashes, so do so intentionally when comparing snapshot histories.

## Data-quality rules

The collector distinguishes **fatal errors** from **reportable warnings**:

| Check | Current behavior |
| --- | --- |
| API/request error, malformed API response, or too few rows | Fail collection; write a failure report; do not write a snapshot. |
| Missing/null API business key | Fail normalization; do not write a snapshot. |
| Exact duplicate API record (same key and content) | Drop duplicate and count it. |
| Conflicting duplicate API keys | Keep first and warn by default; optionally fail with `fail_on_duplicate_keys=true`. |
| Missing required values, unparseable dates, schema drift | Record quality findings; not automatically fatal. |
| Large row-count change | Warn rather than fail. |
| Advisory page that cannot be parsed into records | Fail that collection; preserve raw HTML for investigation/replay. |

Snapshots are content-addressed by a hash computed over sorted `(business_key, content_hash)` pairs. An unchanged pull still creates an entry in `manifest.json -> runs`, but it does not create another snapshot file.

## Scheduling and observability

The [GitHub Actions `collect` workflow](.github/workflows/collect.yml) is configured to run **daily at 11:17 UTC (7:17 p.m. Philippine time)** and can also be launched manually from **Actions → collect → Run workflow**. GitHub-hosted scheduled runs may start later than the configured time.

Each workflow run:

1. Installs dependencies and runs offline `pytest` tests.
2. Executes the construction-permit API collector and both DOT advisory scrapers.
3. Commits persisted snapshots, manifests, reports, and logs to `main` when files change.
4. Uploads the latest reports as a GitHub Actions artifact.
5. Marks the job failed if a collector failed, after saving available diagnostic output.

The GitHub workflow status is the current failure signal; **external alerts (for example, n8n webhook notifications) are not implemented**.

**Storage policy:** API raw pulls are kept locally under `data/raw/` and excluded from Git because they can be large. Snapshot history and reports are committed; small raw DOT advisory HTML files are retained to make parser troubleshooting and replay possible. Git-based storage is suitable for this small portfolio pipeline but would need reevaluation as history grows.

## Repository structure

```text
.github/workflows/collect.yml   Scheduled collection, tests, and report artifacts
config/settings.example.json   Source, schema, quality, and output settings
src/
  run.py                        API collector CLI / orchestrator
  ingest.py                     Socrata pagination, retries, raw persistence
  normalize.py                  Standardization, keys, hashes, quality checks
  snapshot.py                   Versioned snapshots and manifest
  compare.py                    Added/modified/unchanged/missing differences
  report.py                     JSON and Markdown reports
  adapters/dot_advisory.py      Weekly and weekend DOT HTML scraper
tests/                          Fixture-based unit and integration tests
data/sample/                    Publishable synthetic examples
data/snapshots/                 API snapshot history and manifest
data/reports/                   API run reports
data/advisory/                  Separate weekly and weekend histories
logs/                           Collection logs
docs/                           Implementation and CI/CD learning notes
```

For implementation details, see [V1 pipeline explanation](docs/02-v1-explained.md) and [V2 scheduling/observability explanation](docs/03-v2-explained.md).

## Limitations and next steps

**Current limitations:** This is a historical *observation* system, not a verified real-time road-status service. Permit start/end dates indicate authorized work windows, not continuous closure hours. The source only becomes historically observable **after collection begins**. Filtering the API by permit dates restricts which records are included, and two distinct DOT sources must not be treated as interchangeable. Advisory keys depend on textual labels, and page-layout changes may require parser maintenance.

**Planned validation and enhancements:**

- [ ] Observe and verify successive scheduled runs, including unchanged-data idempotency.
- [ ] Exercise an intentionally failed collection and confirm the workflow preserves its failure report.
- [ ] Add external failure/change notifications (potentially an n8n webhook).
- [ ] Revisit storage design if the committed snapshot/report history becomes too large for Git.

## Data sources and references

- [NYC Open Data — Street Closures due to Construction Activities by Block](https://data.cityofnewyork.us/d/i6b5-j7bu)
- [Socrata SODA API documentation](https://dev.socrata.com/docs/endpoints.html)
- [NYC DOT Weekly Traffic Advisory](https://www.nyc.gov/html/dot/html/motorist/weektraf.shtml)
- [NYC DOT Weekend Traffic Advisory](https://www.nyc.gov/html/dot/html/motorist/wkndtraf.shtml)

---

*Independent portfolio project. Information comes from public agency sources and should not be used as a substitute for official, current travel advisories.*
