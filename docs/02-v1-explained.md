# V1 Technical Guide: Data Ingestion, Normalization, Snapshots, and Change Detection

> **Scope:** Local pipeline behavior and source adapters  
> **Primary entry points:** `src/run.py` and `src/adapters/dot_advisory.py`  
> **Related documents:** [Implementation case study](01-what-happened-today.md) · [V2 operations](03-v2-explained.md) · [README](../README.md)

## 1. The problem and the system boundary

The NYC Road Closure Monitor addresses a historical-observation problem: public construction-permit and advisory sources describe a current published state, but do not provide a complete, durable comparison with every earlier publication.

The pipeline therefore answers questions such as:

- Which business records appeared in the latest observation?
- Which previously observed records have different tracked fields?
- Which records have stopped appearing in the collected source?
- Which source and observation timestamps support these conclusions?
- Did an apparent change arise from the upstream data, or from parsing, key selection, or configuration?

The system **does not assert live road conditions**. A published permit window is not proof of a continuous closure, and a `missing` record is not evidence that the road reopened. It only means that a formerly observed record is absent from the latest retrieved state.

The source feeds also have distinct meanings:

| Feed | Ingestion | Record identity |
| --- | --- | --- |
| NYC Open Data construction permits by block | Socrata API, structured JSON | Configured business key, currently `uniqueid` |
| NYC DOT Weekly Traffic Advisory | HTML parsing | Derived key from page/kind/section/name |
| NYC DOT Weekend Traffic Advisory | HTML parsing | Derived key from page/kind/section/name |

The adapters maintain separate historical state and reuse the same downstream snapshot and comparison functions.

## 2. Pipeline architecture and contracts

```text
                    Source API / source HTML
                              |
                     Fetch + retrieval log
                              |
                     Raw local archive
                              |
                Normalize / parse records
                              |
                      Data-quality checks
                              |
                Canonical record interface
                   key / fields / hash
                              |
                Snapshot + manifest update
                              |
               Compare previous vs current
                              |
                  JSON and Markdown report
```

The main API collector is decomposed into modules:

| Module | Responsibility | Important contract |
| --- | --- | --- |
| `src/config.py` | Read and validate settings into configuration objects | Resolves paths against a project root |
| `src/ingest.py` | Fetch source pages, collect metadata, save raw data | Complete response or `FetchError` |
| `src/normalize.py` | Standardize fields, produce keys and content fingerprints | Normalized records plus `QualityReport`, or fatal normalization error |
| `src/snapshot.py` | Persist new states and run attempts | One latest snapshot pointer and an append-only run list |
| `src/compare.py` | Compare successive normalized states | Four categories and field-level modifications |
| `src/report.py` | Publish machine-readable and readable reports | Explicit success/failure status |
| `src/run.py` | Coordinate API collection and expose CLI modes | `0` for success, `1` for handled failure |
| `src/adapters/dot_advisory.py` | Fetch and parse the separate DOT HTML feeds | Shares the snapshot/compare/report modules |

### The canonical record shape

Downstream modules operate on a common conceptual structure:

```json
{
  "key": "PERMIT-IDENTIFIER",
  "content_hash": "sha256-of-canonical-business-fields",
  "fields": {
    "onstreetname": "EXAMPLE STREET",
    "work_end_date": "2026-11-30"
  },
  "original": {
    "onstreetname": "Example Street",
    "work_end_date": "2026-11-30T00:00:00"
  },
  "system": {}
}
```

This is an **illustrative schema**, not a copy of a live record. The key establishes logical identity. The hash represents tracked content. `fields` contains normalized values used for comparisons, `original` retains source values of tracked fields, and `system` holds retained Socrata metadata when present. The advisory parser produces compatible records from HTML instead of from JSON.

**Separation of concerns:** The comparison layer does not need to know whether a record came from Socrata or an HTML page. It only requires a key, a content hash, and comparable business fields.

## 3. Step 1 — Retrieve data reliably (`src/ingest.py`)

### 3.1 Paginated API requests

The Socrata API is requested with parameters equivalent to:

```text
$limit  = 1000
$offset = 0, 1000, 2000, ...
$order  = :id
$select = :*,*
```

`fetch_all()` repeatedly requests pages until a page contains fewer rows than `page_size`. Explicit ordering reduces unstable page boundaries; it does **not** make offset pagination transactional if the upstream dataset changes during collection.

For the configured permit-date window, the query also contains a `$where` overlap condition:

```text
work_end_date >= window_start
AND work_start_date <= window_end
```

This retrieves permit records whose date ranges overlap the selected interval. It is not an observation-date filter. The example configuration currently filters to **2026-11-01 through 2027-01-31**. Setting `source.window` to `null` removes the filter.

### 3.2 Retries, timeouts, and errors

`_get_with_retries()` handles HTTP 429 and retryable 5xx responses with bounded exponential backoff. Non-retryable HTTP responses such as 404 fail immediately. The response must parse into the expected JSON type; a malformed or unexpected payload raises `FetchError`.

The retry policy is configurable through `max_retries`, `backoff_seconds`, and `timeout_seconds`.

**Why failure must remain explicit:** If an API outage were represented as a successful empty list, every record in the preceding snapshot could be falsely reported as `missing`. The API orchestrator instead writes a failure report and does not persist a new snapshot for handled retrieval failures.

### 3.3 Capture source and observation metadata

The collector saves retrieval metadata including request attempts and UTC observation time. A separate Socrata metadata request obtains `rowsUpdatedAt` when available.

| Time concept | Stored value | Interpretation |
| --- | --- | --- |
| Event interval | `work_start_date` / `work_end_date` | Permit authorization dates, not measured closure duration |
| Observation | `fetched_at_utc` | Time the collector fetched the published source |
| Source update | `source_rows_updated_at_utc` | Last-updated timestamp reported by the source metadata |

These timestamps answer different questions and should not be substituted for one another.

### 3.4 Raw persistence and replay

A successful API response is saved as compressed JSON with its retrieval metadata:

```text
data/raw/<run_id>.raw.json.gz
```

The command below replays a previously saved raw response without network access:

```bash
python -m src.run --config config/settings.json --from-raw data/raw/<run_id>.raw.json.gz
```

Because raw API files may contain large geometry fields, `data/raw/` is Git-ignored. They remain available only wherever they were retained locally, not automatically in the public repository.

## 4. Step 2 — Normalize and validate (`src/normalize.py`)

### 4.1 Canonicalization

The normalizer applies deterministic transformations before computing content hashes:

- Text: strip surrounding whitespace, collapse internal whitespace, uppercase.
- Dates: parse supported source formats and convert valid dates to `YYYY-MM-DD`.
- Keys: normalize configured key fields and join parts for composite keys.
- Business fields: serialize normalized values as sorted-key JSON before SHA-256 hashing.

For example, `"  Broadway  "` and `"BROADWAY"` become the same canonical text. This prevents formatting-only source differences from creating unnecessary change events.

The hash is intentionally based on `schema.business_fields`, which excludes volatile Socrata `:id` or `:updated_at` values and currently excludes `the_geom`. Tracking a different set of business fields changes the definition of what counts as a modification.

### 4.2 Key validation and duplicate handling

The configured construction key is `uniqueid`. The source has published multiple rows with the same key.

| Condition | Current behavior |
| --- | --- |
| Repeated key and identical content hash | Drop repeated row; increment `exact_duplicate_rows` |
| Repeated key and different content hash | Keep the first encountered record; report `duplicate_keys` |
| Null business key | Raise `NormalizeError`; do not produce a new snapshot |
| Conflicting key with `fail_on_duplicate_keys=true` | Raise `NormalizeError` |

A key collision with conflicting content is not equivalent to a harmless exact duplicate. The distinction is central to reliable historical comparisons.

### 4.3 Quality-report semantics

`QualityReport` tracks raw input row count, output row count, exact duplicates, collisions, null keys, required-field omissions, date parsing problems, and schema drift. The most important flags are:

| Finding | Fatal by default? | Reason |
| --- | --- | --- |
| Null key | Yes | Record identity is unusable |
| Conflicting duplicate keys | No; configurable | Flagged, with first record retained |
| Missing required business value | No | Reported for quality review |
| Unparseable date | No | Reported, normalized value becomes `null` |
| Unexpected/absent fields | No | Schema-drift warning |
| Too few API rows | Yes | Avoids a suspiciously empty snapshot |
| Large row-count swing | No | Warning only |

The `quality.ok` property flags duplicate-key collisions, missing required values, or null keys; it does not alone decide whether the orchestrator exits with failure.

**Implementation detail:** In the current `report.py`, the JSON field `counts.rows_normalized` is populated from `quality.row_count` (the input count), not `quality.output_rows` (the deduplicated count). For post-deduplication counts, the snapshot manifest's `row_count` and the normalization quality payload's `output_rows` are more precise. This naming inconsistency is a potential improvement and should not be interpreted as a data-loss incident.

## 5. Step 3 — Persist historical state (`src/snapshot.py`)

### 5.1 Snapshot versus run

A snapshot is a compressed array of normalized records:

```text
data/snapshots/<run_id>.snapshot.json.gz
```

A manifest keeps two collections:

- `snapshots`: persisted distinct states, each with hash, path, row count, observation time, and quality information.
- `runs`: collection attempts and outcomes, including failed attempts and successful unchanged observations.

A failed run may have no snapshot. A successful unchanged run may reference an existing snapshot.

### 5.2 Dataset fingerprint and idempotency

The whole-state fingerprint is constructed from sorted pairs:

```text
sorted((record.key, record.content_hash) for record in normalized_records)
        -> canonical JSON
        -> SHA-256 snapshot_hash
```

`snapshot.persist()` compares this fingerprint against the **latest** snapshot. When equal, it records `snapshot_written=false` and `deduplicated_to=<previous_run_id>`; otherwise, it writes the new snapshot and appends a snapshot entry.

```text
First observation X  -> run 1 + snapshot A
Repeat observation X -> run 2 (references A), no new snapshot
Changed state Y      -> run 3 + snapshot B
```

The design is idempotent at the level of **persisted dataset states**, not at the level of run records: every successful invocation still adds a run attempt.

### 5.3 Historical contract

A comparison assumes that the business key, tracked fields, normalization logic, and source filter have compatible meanings across observations. Changing `source.window` or `schema.business_fields` can change the population or the hash definition even when underlying permits are unchanged.

Such configuration changes should be treated as **comparison-contract changes** and documented rather than mistaken for business events.

## 6. Step 4 — Compare observations (`src/compare.py`)

The comparison function creates indexes keyed by logical record identity and classifies each key:

| Classification | Condition |
| --- | --- |
| `added` | Present only in current |
| `modified` | Present in both, different content hashes |
| `unchanged` | Present in both, same content hash |
| `missing` | Present only in previous |

For a modified record, `field_diff()` reports precisely which tracked fields changed:

```json
{
  "work_end_date": {
    "from": "2026-11-20",
    "to": "2026-12-05"
  }
}
```

On the first successful collection, no preceding snapshot exists. Every retained record therefore falls into `added`; this is a **baseline classification**, not proof that all permits were newly issued.

Likewise, `missing` is limited to source presence. It is not a reopening event.

The repository includes a [synthetic sample report](../data/sample/example.report.md) that demonstrates all four categories across two fixture snapshots.

## 7. Step 5 — Publish reports (`src/report.py`)

Each run writes JSON and Markdown, together with overwritten latest pointers:

```text
data/reports/<run_id>.report.json
data/reports/<run_id>.report.md
data/reports/latest.report.json
data/reports/latest.report.md
```

The machine-readable report contains `run_id`, `status`, `error`, `warnings`, source metadata, counts, quality details, snapshot details, the previous comparison run ID, and diff content when available.

The Markdown report summarizes the run and shows a sample of changed records. A failure report carries `status="failure"` and no meaningful difference count; it must not be processed as a valid empty observation.

Reports are the interface for inspection and potential downstream automation. No external alert webhook is currently required for local V1 behavior.

## 8. The orchestrator (`src/run.py`)

The API entry point coordinates the modules in this order:

```text
load settings
  -> create run ID and log
  -> live fetch OR raw replay
  -> check source row volume
  -> load latest snapshot manifest
  -> normalize and collect quality findings
  -> persist state or link unchanged state
  -> load previous snapshot and compare
  -> write JSON/Markdown report
  -> return exit code
```

Main commands:

```bash
# First setup
cp config/settings.example.json config/settings.json

# Normal API collection
python -m src.run --config config/settings.json

# Offline replay
python -m src.run --config config/settings.json --from-raw data/raw/<run_id>.raw.json.gz

# Candidate-key audit
python -m src.run --config config/settings.json --key-check uniqueid segmentid

# Test suite
python -m pytest
```

A handled fetch/normalization/volume failure goes through `_fail()`, which writes a failure run and report with exit code `1`. Unexpected programming exceptions are not universally caught by `_fail()` and can terminate the process before report creation; the CI workflow's failing step remains an additional signal.

## 9. Separate source adapter: NYC DOT advisories

`src/adapters/dot_advisory.py` fetches and parses Weekly and Weekend Traffic Advisory HTML. The pages contain a mix of closure descriptions and special-event details; they are not interchangeable with API construction permits.

The parser traverses `h2`, `h3`, `h4`, `strong`, `p`, and `ul` elements. It extracts publication periods, borough sections, descriptions, updated flags, event labels, embargo notes, and street lists.

### Derived record keys

The key is derived from `page | kind | section | name` and a truncated SHA-1 hash. Since there is no official notice-level ID, renaming an advisory may produce `missing` + `added` instead of `modified`. This is an explicit limitation of identity resolution, not evidence of a physical road closure transition.

### Parsing safeguards

- HTML responses below a minimum size cause a fetch error.
- Pages that cannot yield any records raise `ParseError` rather than producing an empty successful snapshot.
- Duplicate derived keys become parse warnings; they do not currently prevent a snapshot from being written.
- Raw HTML is retained in the repository to support offline replay and parser regression tests.
- A failure in one configured advisory page does not prevent the next page from being attempted.

The adapter creates independent trees under `data/advisory/weekly/` and `data/advisory/weekend/`.

```bash
python -m src.adapters.dot_advisory --config config/settings.json
python -m src.adapters.dot_advisory --config config/settings.json --page weekend
python -m src.adapters.dot_advisory --config config/settings.json --page weekly --from-html data/advisory/weekly/raw/<run_id>.advisory.html.gz
```

## 10. How the tests establish correctness

Tests live in `tests/` and run with `python -m pytest`. Fake sessions replace live HTTP calls, while pytest's `tmp_path` fixture isolates generated snapshots and reports.

| Test file | Main concerns |
| --- | --- |
| `test_ingest.py` | Pagination, retryable/nonretryable responses, invalid JSON structures, date-window parameters |
| `test_normalize.py` | Canonicalization, hashing, exact duplicates, collisions, null keys, schema drift |
| `test_compare.py` | First-run, unchanged, modified, missing, and added classifications |
| `test_run.py` | End-to-end persistence, idempotency, failed fetch, blank source, volume warnings, offline replay |
| `test_dot_advisory.py` | HTML parsing, publication periods, structure variations, key stability, replay, page isolation and failure behavior |

The core end-to-end invariant is:

```text
Invalid / unavailable source -> failure report, no new valid snapshot
Unchanged valid source       -> successful run, no duplicate snapshot
Changed valid source         -> new snapshot, correct diff categories
```

Fixture tests verify deterministic behavior. Live tests remain necessary for assumptions about source schemas, page markup, and network behavior.

## 11. Diagnosing problems

| Symptom | First evidence to inspect | Likely next action |
| --- | --- | --- |
| API collector returns code 1 | `data/reports/latest.report.md` and run log | Inspect HTTP error, row-volume threshold, or key failure |
| Unexpectedly many `modified` records | Configured business fields and source metadata | Check whether comparison contract changed |
| Unexpected `missing` records | Permit window, raw rows, source freshness | Verify source/filter before inferring disappearance |
| High exact-duplicate count | `quality.exact_duplicate_rows` | Inspect duplicated business keys in raw data |
| Key collisions | `quality.duplicate_keys_sample` | Reassess key definition and strictness |
| Advisory parser fails | Saved `.advisory.html.gz` and parser tests | Reproduce through `--from-html`; update fixture and parser |
| Same source, different output across environments | Python and dependency versions | Compare parser behavior and environment versions |
| Duplicate snapshots for unchanged content | `manifest.json` and snapshot hash | Inspect canonicalization and persisted-state comparison |

## 12. Scope and next architectural layer

This project borrows Bronze/Silver terminology for local raw and normalized history, but it is not a Databricks medallion pipeline. Gold-style reporting tables and dimensional models are **not implemented**.

The natural future extensions are reliable long-term object storage, structured operational metrics, improved advisory identity resolution, and downstream analytical marts—after the existing snapshot history has been validated over multiple observations.

---

**Further reading:** [Development case study](01-what-happened-today.md) · [V2 scheduled operations](03-v2-explained.md) · [Sample report](../data/sample/example.report.md)
