# V2 Technical Guide: GitHub Actions Scheduling, Persistence, and Observability

> **Scope:** Automated operation of the existing V1 collectors  
> **Workflow:** [`.github/workflows/collect.yml`](../.github/workflows/collect.yml)  
> **Related:** [V1 pipeline guide](02-v1-explained.md) · [Implementation case study](01-what-happened-today.md) · [README](../README.md)

## 1. What V2 adds

V1 performs source collection, validation, historical snapshotting, and comparison when invoked manually. V2 adds an **execution and operations layer**: runs occur on a schedule, historical outputs survive the runner, and failures are visible through GitHub Actions.

These are distinct concerns:

| Concern | V1 responsibility | V2 responsibility |
| --- | --- | --- |
| Correctness | Fetch, normalize, check quality, diff | Run tests before collection |
| Execution | Python command-line entry points | Schedule and manual GitHub Actions triggers |
| Persistence | Local snapshots, manifests, reports | Commit selected outputs back to GitHub |
| Isolation | One source-specific pipeline per command | Execute API and advisory collectors separately |
| Failure handling | Failure reports and nonzero exit codes | Preserve available evidence; mark failing workflow |
| Observability | JSON, Markdown, logs | GitHub run status, commits, and report artifacts |

**Operational objective:** Repeated execution should be safe for the snapshot history, source failures should not masquerade as legitimate data changes, and run results should be inspectable without access to the runner's temporary filesystem.

## 2. GitHub Actions execution model

A **workflow** is a YAML definition stored under `.github/workflows/`. A **run** is one execution of that definition. A workflow contains **jobs**, and each job contains sequential **steps** executed by a runner.

The `collect` workflow has a single job on `ubuntu-latest`. That job uses a newly provisioned, temporary environment. Files generated during the run do not automatically survive after the job ends.

Consequently, a history-producing pipeline cannot rely only on runner-local paths for long-term persistence. In this project, selected outputs are **committed back to the repository**.

### Why not just use a scheduled task on a laptop?

A local scheduler requires the machine to be powered on, correctly configured, and connected at the scheduled time. GitHub Actions supplies managed execution, repository integration, access to secrets, workflow history, and downloadable artifacts. It does not guarantee exact execution time, and it is not a substitute for dedicated production orchestration.

## 3. Workflow triggers and concurrency

The relevant workflow configuration is:

```yaml
name: collect

on:
  schedule:
    - cron: "17 11 * * *"
  workflow_dispatch:

concurrency:
  group: collect
  cancel-in-progress: false
```

### Scheduled execution

The cron expression `17 11 * * *` means **11:17 UTC every day**, equivalent to **19:17 Philippine time**. It is not a local-machine time setting. Scheduled runs can be delayed under GitHub load, and scheduled workflows can be disabled after prolonged repository inactivity under GitHub's platform policies.

`workflow_dispatch` also makes the workflow manually executable from GitHub's Actions interface.

### Concurrency control

All runs share concurrency group `collect`. This prevents two workflow instances from running simultaneously and writing the same manifest at the same time. `cancel-in-progress: false` avoids canceling a running instance when another is triggered.

GitHub concurrency is not an unlimited FIFO queue: new pending runs may replace earlier pending runs. The setting reduces overlap but does not guarantee every requested execution will eventually run.

**Why it matters:** A manifest is read, updated, and committed as a unit. Concurrent writers could each base an update on an old state, resulting in conflicts or lost history.

## 4. Permissions and secrets

The workflow requests:

```yaml
permissions:
  contents: write
```

This lets the run's `GITHUB_TOKEN` write repository contents as allowed by repository and organization settings. It is needed because the workflow creates commits and pushes them to `main`.

The Socrata token is passed only to the API collector step:

```yaml
- name: API collector (NYC Open Data construction closures)
  id: api
  env:
    SOCRATA_APP_TOKEN: ${{ secrets.SOCRATA_APP_TOKEN }}
  run: python -m src.run --config config/settings.json
  continue-on-error: true
```

The `env` entry uses GitHub's standard expression syntax to reference the `SOCRATA_APP_TOKEN` repository secret.

The secret is optional; the API can be queried without it but may encounter stricter rate limits. Repository secrets belong in **Settings → Secrets and variables → Actions**, not in `config/settings.example.json` or the public Git history.

**Security boundary:** A commit author name is not an authentication mechanism. The workflow authenticates its push with GitHub's short-lived token and uses `github-actions[bot]` as the commit identity.

## 5. Step-by-step workflow lifecycle

The [workflow file](../.github/workflows/collect.yml) contains ten steps.

### Step 1 — Checkout

```yaml
- uses: actions/checkout@v4
```

Checks out source files and existing committed data history onto the runner. The checkout is important because the manifest stored in Git provides the baseline for the next comparison.

### Step 2 — Set up Python

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"
```

Pins the Python major/minor interpreter version for more predictable runtime behavior. The `ubuntu-latest` environment and dependency versions are not fully immutable; further pinning would be needed for strict reproducibility.

### Step 3 — Install dependencies

```yaml
- name: Install dependencies
  run: pip install -r requirements.txt
```

Installs `requests`, `beautifulsoup4`, and `pytest` from project dependency constraints.

### Step 4 — Run tests

```yaml
- name: Tests (fixtures only, no network)
  run: python -m pytest
```

The tests run before either source collector. Because this step does **not** set `continue-on-error`, a failing test prevents the later collection commands from running normally.

The tests use local fixtures and fake HTTP sessions. They validate program behavior but cannot prove that the live API or DOT pages have not changed.

### Step 5 — Create runtime configuration

```yaml
- name: Config from example
  run: cp config/settings.example.json config/settings.json
```

The ignored local `settings.json` does not exist in a fresh checkout. The public example is copied into place for the runner. Therefore, `config/settings.example.json` is both a documentation template and the workflow's production configuration source.

A change to the example date window, dataset ID, schema, or quality threshold affects the next scheduled collection.

### Step 6 — Execute the API collector

```yaml
- name: API collector (NYC Open Data construction closures)
  id: api
  env:
    SOCRATA_APP_TOKEN: ${{ secrets.SOCRATA_APP_TOKEN }}
  run: python -m src.run --config config/settings.json
  continue-on-error: true
```

The collector fetches the configured construction-permit dataset, validates and normalizes records, updates the API manifest, compares observations, and writes reports.

`continue-on-error: true` lets the workflow proceed to the advisory collector and persistence steps even when this command exits nonzero. The step's `outcome` still retains the failure signal.

### Step 7 — Execute the advisory collectors

```yaml
- name: Advisory scrapers (weekly + weekend)
  id: advisory
  run: python -m src.adapters.dot_advisory --config config/settings.json
  continue-on-error: true
```

The command attempts every configured advisory page. Each page has a separate manifest and report tree. A page failure does not prevent the adapter from attempting other pages; the command exits nonzero if any page fails.

This step also continues after failure so that available output can be committed.

### Step 8 — Commit generated history

The workflow configures the Git author as `github-actions[bot]`, then stages `data` and `logs`:

```bash
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data logs

if git diff --cached --quiet; then
  echo "nothing to commit"
else
  git commit -m "collect: <UTC timestamp> api=<outcome> advisory=<outcome>"
  git pull --rebase origin main
  git push origin HEAD:main
fi
```

The example above abbreviates the actual dynamically generated commit message; the repository workflow includes GitHub expressions that insert the observed step outcomes.

The essential details are:

- `git add data logs` respects `.gitignore`, so API raw files under `data/raw/` are excluded.
- The manifest's run list generally changes after a collector attempt even if no new snapshot is needed.
- `git diff --cached --quiet` avoids committing when nothing was staged.
- `git pull --rebase origin main` attempts to replay the generated commit on the latest remote branch state.
- `git push origin HEAD:main` persists history to the repository.

**Operational risk:** A rebasing conflict, missing write permission, branch restriction, or push failure can prevent persistence. The collector's local success does not imply that the generated history reached GitHub.

### Step 9 — Upload report artifacts

```yaml
- name: Upload latest reports as run artifact
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: reports-${{ github.run_id }}
    path: |
      data/reports/latest.report.*
      data/advisory/*/reports/latest.report.*
    if-no-files-found: ignore
```

This attaches the latest reports to the GitHub Actions run. `if: always()` makes the artifact step eligible even if an earlier step failed.

Artifacts are not the same as source-control history. Their retention depends on GitHub configuration and applicable platform policies. Additionally, an artifact is useful only if report files were generated before the failure.

### Step 10 — Mark a collector failure at the job level

```yaml
- name: Fail the job if any collector failed
  if: steps.api.outcome == 'failure' || steps.advisory.outcome == 'failure'
  run: exit 1
```

The failure signal is evaluated **after** the persistence and artifact steps. This lets collection failures produce durable diagnostic evidence when the remaining operations complete.

The workflow can fail for other reasons too—tests, setup, artifact upload, or Git push—even if this explicit final failure step does not run.

## 6. Why `outcome` matters with `continue-on-error`

In GitHub Actions, `continue-on-error: true` permits later steps to run. However, this setting changes how the step contributes to job success.

Two related contexts matter:

- `steps.<id>.outcome`: the underlying result before `continue-on-error` is applied.
- `steps.<id>.conclusion`: the result after `continue-on-error` processing.

The workflow checks `steps.api.outcome` and `steps.advisory.outcome`, preserving the real error signal for the final failure gate.

Without a final failure gate, an operationally failed collector could appear inside a successful job because its own error had been explicitly tolerated.

## 7. Persistence: what survives a workflow run?

| Artifact | Stored in Git? | Notes |
| --- | --- | --- |
| API normalized snapshots and manifest | Yes | New snapshots only when the normalized dataset changes |
| API JSON/Markdown reports | Yes | Generated per collection attempt and latest pointers |
| API raw responses under `data/raw/` | **No** | Ignored; runner copy disappears when the job ends |
| Weekly/weekend raw advisory HTML | Yes | Small raw pages retained to reproduce parser behavior |
| Advisory snapshots and manifests | Yes | Independent source histories |
| Logs | Yes | Useful for diagnosing retrieval and parser errors |
| Latest reports as Actions artifacts | Separately | Downloadable from workflow runs, subject to retention |

**Important reproducibility boundary:** The workflow's raw Socrata API response is not retained in Git, and the temporary runner is destroyed. A future API request may return different data. Therefore, an old scheduled API execution cannot necessarily be replayed byte-for-byte from the public repository; only its normalized snapshot and metadata are persisted. This is a trade-off between storage size and full raw lineage.

Git-based storage is appropriate at this project's current size, but it scales poorly for growing binary snapshots and large frequently rewritten JSON reports. Object storage would be a more scalable future design.

## 8. Operational interpretation of reports

The most useful inspection paths are:

| Question | Location |
| --- | --- |
| Did execution complete successfully? | GitHub **Actions → collect** run status |
| Did the collectors fail? | Workflow step outcomes and `latest.report.json` `status` |
| What changed in the API feed? | `data/reports/latest.report.md` |
| What changed in weekly notices? | `data/advisory/weekly/reports/latest.report.md` |
| What changed in weekend notices? | `data/advisory/weekend/reports/latest.report.md` |
| What was the previous persisted state? | `data/snapshots/manifest.json` or per-advisory manifest |
| Was a new snapshot written? | Run entry's `snapshot_written` and `deduplicated_to` |
| What retries or warnings occurred? | Run-specific files under `logs/` |

There are three distinct concepts:

1. **Run status:** Whether the collector or workflow completed successfully.
2. **Dataset state:** Whether the normalized content differs from the previous snapshot.
3. **Real-world event:** Whether a road was actually closed or reopened.

A green workflow may create no new snapshot. A newly missing record does not necessarily represent a reopening. A failed workflow may leave unchanged historical snapshots.

## 9. Failure scenarios and first-line diagnosis

| Symptom | Possible cause | Diagnostic action |
| --- | --- | --- |
| Test step fails | Source code or test regression | Run `python -m pytest` locally; inspect failed assertions |
| API collector: HTTP 404 | Dataset ID incorrect or removed | Check `source.dataset_id` and public dataset URL |
| API collector: HTTP 400 | Invalid `$where` or schema mismatch | Review window fields and source schema |
| API collector: repeated HTTP 429/503 | Rate limiting or transient service outage | Examine retries; check optional app token and source availability |
| API collector: too few rows | Filter excludes all data or source returned suspicious volume | Inspect `source.window` and `quality.min_rows` |
| API collector: null key | Missing configured identity field | Inspect source rows and `schema.key_fields` |
| Advisory: `ParseError` | Unexpected HTML layout or no recognized notices | Inspect saved HTML; extend parser regression fixtures |
| Advisory: duplicate-key warning | Two notices derive the same key | Inspect the affected page and identity rule |
| Commit step: permission denied | Token permissions, branch restrictions, or repo policy | Inspect `contents: write` and repository workflow permissions |
| Commit step: rebase conflict | Concurrent edits to committed data/manifest | Resolve conflict from latest main; rerun after reconciliation |
| No scheduled run | Delay, disabled workflow, or scheduler behavior | Inspect Actions status and workflow settings |
| Collection succeeded but no new snapshot | Source content unchanged | Inspect `snapshot_written=false` and dataset hash |

The GitHub Actions log is the first place to inspect for failures that happen **before** the Python application can write its own report.

## 10. Operational validation

A useful acceptance checklist for this automation design is:

- [ ] A manually dispatched workflow completes successfully.
- [ ] A scheduled workflow completes successfully.
- [ ] A second successful unchanged observation does not produce a duplicate snapshot.
- [ ] A source failure produces a failure report and no new successful snapshot.
- [ ] A collector failure remains visible in the final GitHub Actions job result.
- [ ] Available reports and logs remain accessible after the runner is destroyed.
- [ ] A controlled re-run does not produce duplicate or conflicting manifest entries.

A source-failure demonstration is possible in a controlled environment by temporarily using an invalid dataset identifier and observing the resulting failure report, followed by restoration of the valid configuration. Performing such a test on the active `main` branch affects the scheduled workflow and therefore requires care. Fixture-based failure tests provide a safer default.

The presence of a scheduled workflow file is not itself proof that the schedule has executed successfully for multiple days; the actual Actions run history must be checked.

## 11. Known limitations and possible extensions

### Current boundaries

- GitHub scheduled execution is not an exact-time service-level guarantee.
- The workflow records failures but does not currently implement an external webhook or guaranteed paging notification.
- The Git repository is serving as lightweight operational storage, not as a long-term object store.
- Raw API payloads from ephemeral scheduled runs are excluded from persisted history.
- The Git push and manifest update are not a transactional database operation.
- Live page parsing and upstream API availability remain dependencies outside the repository's control.

### Possible improvements

| Improvement | Benefit | Status |
| --- | --- | --- |
| External notification on collector failure or meaningful change | Faster operational response | Not implemented |
| Object-storage archive for source raw responses | Reproducible reprocessing and scalable history | Not implemented |
| Explicit comparison-schema versioning | Distinguish business changes from transformation contract changes | Not implemented |
| Stronger concurrency/persistence controls | Safer multi-writer or larger-scale operation | Not implemented |
| Operational dashboard | Trend monitoring for volumes, failures, freshness, and DQ | Not implemented |

The goal is not to expand the stack unnecessarily. Each addition should address a demonstrated reliability, observability, or user-value requirement.

## 12. Command reference

```bash
# Run tests
python -m pytest

# Run the API collector manually
python -m src.run --config config/settings.json

# Run both DOT advisory pages
python -m src.adapters.dot_advisory --config config/settings.json

# Replay a locally retained API response
python -m src.run --config config/settings.json --from-raw data/raw/<run_id>.raw.json.gz

# Review local Git state
git status
git log --oneline -10

# Inspect the committed manifest history
git log -- data/snapshots/manifest.json
```

For a clean local run, the ignored configuration must first be created from `config/settings.example.json`.

---

**Further reading:** [V1 pipeline and tests](02-v1-explained.md) · [Engineering decisions and lessons](01-what-happened-today.md) · [Current GitHub workflow](../.github/workflows/collect.yml)
