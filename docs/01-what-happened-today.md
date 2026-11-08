# Implementation Case Study: From Prototype to Scheduled Monitoring

> **Document type:** Engineering case study and design-decision log  
> **Period:** Initial implementation and validation, November 2026  
> **Companion guides:** [V1 pipeline architecture](02-v1-explained.md) · [V2 scheduling and operations](03-v2-explained.md) · [README](../README.md)

## Purpose

This document explains **how the NYC Road Closure Monitor evolved**, which assumptions were tested, what defects were encountered, and how those findings shaped the architecture. It is a historical engineering case study, not a transcript of an individual's setup session.

NYC publishes construction-related street-closure permits and separate traffic-advisory pages. The published state can change or be replaced, without a convenient historical change feed. A monitor must therefore collect observations over time, preserve comparable records, and distinguish genuine content changes from ingestion artifacts.

The implementation progressed through three capabilities: a local API collector, separate HTML advisory adapters, and scheduled execution with persistent reports.

## 1. Design decisions

| Decision | Reason | Implementation |
| --- | --- | --- |
| Use the structured Socrata API for construction permits | Reduces parsing complexity and exposes explicit source fields | `src/ingest.py` |
| Keep record identity configurable | Candidate keys must be validated against real source data | `schema.key_fields` |
| Save raw source material | Enables replay when normalization or parsing fails | `data/raw/` and `data/advisory/*/raw/` |
| Hash normalized business fields, not volatile platform identifiers | Avoids false positive modifications after source reloads | `src/normalize.py` |
| Keep immutable snapshots and a manifest | Preserves a series of comparable observations | `src/snapshot.py` |
| Distinguish collection failure from disappearing records | An unavailable API must not be interpreted as every closure disappearing | `src/run.py` |
| Test behavior using synthetic fixtures | Makes retry, comparison, and failure scenarios repeatable without network access | `tests/` |

The module boundaries follow the data flow: **fetch → normalize → snapshot → compare → report**, coordinated by `src/run.py`. This allowed the source-specific advisory scraper to reuse the existing snapshot, comparison, and reporting logic.

## 2. Bootstrap and local environment

The project was scaffolded with independent modules, tests, and a public example configuration before live source assumptions were finalized. For reproducibility, the recommended Windows PowerShell setup is:

```powershell
git clone https://github.com/jg0901/nyc-road-closure-monitor.git
cd nyc-road-closure-monitor
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item config\settings.example.json config\settings.json
python -m pytest
```

A virtual environment keeps project dependencies separate from system Python. The `py` launcher can be useful on Windows installations where `python` is not on `PATH`. Windows PowerShell 5.1 does not support `&&` command chaining; separate lines work consistently.

The committed `settings.example.json` is a template; the local `settings.json` is Git-ignored. Secrets such as `SOCRATA_APP_TOKEN` belong in environment variables or GitHub Actions secrets, not source control.

**Why tests came first:** The behavior of pagination, retries, snapshot identity, and change classification can be validated with known sample rows. However, passing fixture-based tests cannot establish whether the live service's column names and identifiers match assumptions.

## 3. Auditing the business key

A key audit was run against the configured API date window:

```powershell
python -m src.run --config config/settings.json --key-check uniqueid segmentid
```

The early live observation returned:

| Candidate identifier | Retrieved rows | Distinct values | Unique among retrieved rows? |
| --- | ---: | ---: | --- |
| `uniqueid` | 3,689 | 3,530 | No |
| `segmentid` | 3,689 | 1,824 | No |

A nonunique field is not automatically unusable as a logical business key. The crucial follow-up is whether duplicate values represent **identical records** or **conflicting content**.

The source published multiple copies of some permits. The normalizer was adjusted to distinguish:

- **Exact duplicate:** same business key and same canonical content hash; retain one, count the rest.
- **Key collision:** same business key but different content hash; retain the first and record a quality warning, or fail when configured to do so.

The November 7 collection retained **3,530 normalized construction records from 3,689 source rows**, dropping **159 exact duplicates** with no conflicting-key collisions reported.

`segmentid` was not adopted as the logical permit key: a street segment is geographic context, not necessarily a unique permit identity.

**Engineering lesson:** Key selection should be based on source profiling and collision semantics, not field naming conventions alone.

## 4. Discovering schema mismatches

The initial live run surfaced a configuration mismatch: the source supplied `borough_code` instead of the initially expected `borough`. The field `oft` was also incorporated into the tracked business fields.

The relevant construction dataset fields included:

```text
uniqueid, segmentid, oft, onstreetname, fromstreetname,
tostreetname, borough_code, work_start_date, work_end_date,
purpose, the_geom
```

The comparison hash uses configured business fields and excludes Socrata system metadata such as `:id`, which may change after a city-side dataset reload. Geometry is currently excluded from the tracked business-field hash as well.

A schema correction can produce widespread apparent modifications: when the set of hashed fields changes, the comparison contract changes. Consequently, a one-time mass of `modified` records after a configuration change should not automatically be interpreted as source-side updates.

**Engineering lesson:** Hash definitions and business-key rules are part of the historical data contract. Changes to those rules should be documented and distinguished from actual business events.

## 5. Verifying ingestion behavior

The API collector requests pages with `$limit`, `$offset`, and `$order=:id`, and uses timeouts and bounded exponential backoff for transient errors such as HTTP 503.

A complete pull saves the raw response and retrieval metadata locally before normalization. That material can be replayed without another network request:

```powershell
python -m src.run --config config/settings.json --from-raw data/raw/<run_id>.raw.json.gz
```

This separation is useful when diagnosing whether a discrepancy originated in the source, the parser/normalizer, or later business logic. A failed retrieval cannot be silently converted to an empty observation: the collector records a failure report instead of producing an all-`missing` diff.

## 6. Proving idempotency

`src/snapshot.py` computes a hash of the sorted `(key, content_hash)` pairs for a complete normalized observation. A matching hash against the latest snapshot means no new snapshot file is needed, although the run is still recorded.

```text
Run A, dataset X  -> snapshot 1 written; run A recorded
Run B, dataset X  -> no new snapshot; run B linked to snapshot 1
Run C, dataset Y  -> snapshot 2 written; run C recorded
```

This matters when a scheduler executes daily while the upstream source changes less frequently. **Runs measure collection attempts; snapshots measure distinct observed dataset states.**

Idempotency was tested with repeated input and the manifest's `deduplicated_to` field. The repository also includes an offline integration test for this invariant.

## 7. Adding the DOT advisory adapters

Construction-permit records and temporary/event traffic advisories are different data products. The advisory pages were therefore implemented as **separate sources**, each with its own raw files, manifest, snapshots, and reports.

The parser in `src/adapters/dot_advisory.py` interprets the HTML structure:

| Element | Meaning |
| --- | --- |
| Page heading | Advisory period |
| Borough heading (`h2`) | Geographic section |
| Standalone `strong` | Closure name/location |
| `p` | Description |
| Fully bold paragraph | DOT updated-information marker |
| `h3` / `h4` | Event grouping and name |
| Labeled `strong` followed by `ul` | Street lists, routes, formations, dispersals |

Because no stable per-notice IDs are exposed, the adapter derives keys from source page, kind, section, and name. Renaming a location may therefore appear as a missing record followed by an added record.

### HTML parser portability defect

In early validation, the same saved HTML produced different apparent structures in different Python/Beautiful Soup environments. Markup following a `<br />` element appeared nested differently, causing a parser that relied on direct-child relationships to omit records.

The fix was to iterate structural descendants in document order, ignoring elements nested inside another relevant structural element. Regression tests exercise flat and nested markup.

A saved advisory page permits offline replay:

```powershell
python -m src.adapters.dot_advisory --config config/settings.json --page weekly --from-html data/advisory/weekly/raw/<run_id>.advisory.html.gz
```

**Engineering lesson:** For HTML ingestion, storing the original page is essential for diagnosis, especially when page content changes before a defect is investigated.

## 8. Isolating weekly and weekend histories

The two DOT pages share parsing logic but represent different publication windows. The project uses separate directories:

```text
data/advisory/
  weekly/
    raw/
    snapshots/manifest.json
    reports/latest.report.md
  weekend/
    raw/
    snapshots/manifest.json
    reports/latest.report.md
```

Failure in one advisory source does not prevent attempts on other configured pages. The command returns a failure code if any page fails, preserving a clear overall result.

## 9. Making collection scheduled and inspectable

The GitHub Actions workflow adds execution without a persistent local machine. It runs daily at **11:17 UTC (19:17 Philippine time)** and supports manual dispatch.

The workflow:

1. Checks out the repository and sets up Python 3.12.
2. Installs dependencies and runs the offline test suite.
3. Executes the construction collector and both advisory scrapers.
4. Commits generated history, logs, and reports to `main`.
5. Uploads the latest reports as workflow artifacts.
6. Fails the job when a collector failed, after the reporting steps have had a chance to run.

This design preserves evidence of a source failure **provided the workflow reaches and successfully completes its persistence steps**. A failure during dependency installation, testing, or Git push has different implications. The system currently relies on GitHub workflow status for failure visibility; external notifications have not been implemented.

## 10. What this case study demonstrates

| Engineering principle | Concrete application |
| --- | --- |
| Validate assumptions | Source-key audit and schema inspection |
| Fail visibly | Distinguish unavailable sources from empty datasets |
| Preserve evidence | Raw API responses, saved advisory HTML, manifests |
| Avoid false change detection | Normalize values and exclude volatile source metadata |
| Make retries safe | Idempotent content-addressed snapshots |
| Design for testing | Inject fake HTTP sessions, use deterministic fixtures |
| Reuse stable interfaces | API and HTML sources share snapshot/diff/report modules |
| Operate beyond local execution | Scheduled collection, report artifacts, and run logs |

## 11. Boundaries

The project is an **observation history**, not a real-time routing service. Permit dates do not prove continuous closures; absence from a later snapshot does not prove reopening. History begins at the first saved observation.

The November 7 reports are baseline observations, not evidence of a subsequent source change. Meaningful field-level change history requires multiple distinct observations. External alert delivery, object storage, dashboards, and analytical Gold tables remain possible extensions rather than implemented features.

---

**Next:** [Detailed V1 architecture](02-v1-explained.md) · [V2 workflow operations](03-v2-explained.md) · [Example diff report](../data/sample/example.report.md)
