# V1 explained — for someone learning this for the first time

Goal of this doc: after reading it you should be able to open any file in `src/` or `tests/`, know what it is
for, know *why* it is built that way, and be able to debug it yourself when it breaks.

It is organised the way data flows: **fetch → normalize → snapshot → compare → report**, then the parts that
sit around that flow: **config, tests, git, GitHub Actions**, and finally **how to debug**.

---

## Part A — The problem, in one paragraph

NYC publishes a dataset of streets closed for construction. Every week the city **overwrites** it: whatever was
there last week is gone. There is no history. If you want to answer "when did this closure appear? when did it
change? when did it disappear?" you have to collect the dataset yourself, regularly, and compare each pull with
the previous one. That comparison is only trustworthy if (1) you can identify "the same row" across pulls, and
(2) you can tell "changed" from "identical" without false alarms. Everything in `src/` exists to make (1) and (2)
reliable.

---

## Part B — The data flow

### B1. `src/ingest.py` — fetching (the Bronze layer)

**Job:** get *every* row from the API, safely, and keep the raw response.

**Key ideas:**

- **Pagination.** APIs don't return 3,689 rows at once. We ask for 1,000 at a time (`$limit=1000`) starting at
  row 0, 1000, 2000… (`$offset`). We stop when a page comes back with fewer than 1,000 rows. Look at `fetch_all()`:
  it's a `while True` loop that breaks on a short page.
- **Stable ordering.** `$order=:id`. Without an explicit order, the server may return rows in a different order
  per page and you'd get duplicates/misses across page boundaries. Always order when paginating.
- **Retries with backoff.** `_get_with_retries()` tries up to `max_retries` times. On 429/5xx (server busy) it
  waits `backoff × 2^attempt` seconds and tries again. On 4xx (our request is wrong) it fails immediately —
  retrying a 404 just wastes time. You saw this work live: `HTTP 503 (retryable)` → retry → page 3 arrived.
- **A failure is never an empty dataset.** If fetching fails, `fetch_all()` *raises* `FetchError`. It never
  returns `[]`. Why? Because an empty list would look like "all closures disappeared" and produce a diff with
  3,530 "missing" rows. That would be a lie. Raising stops the pipeline and produces a failure report instead.
- **Raw is saved before anything else.** `save_raw()` writes the untouched rows + retrieval metadata (URL,
  `fetched_at_utc`, pages, request log) to `data/raw/<run_id>.raw.json.gz`. If the parser/normalizer has a bug
  later, you can fix it and *replay* (`--from-raw`) without re-fetching. This is "Bronze" in medallion terms.
- **Metadata.** `fetch_dataset_metadata()` asks Socrata for `rowsUpdatedAt` → the city's last-update time.
  That becomes `source_rows_updated_at_utc` in reports. Observation time (`fetched_at`) ≠ source time.
- **App token.** Read from the environment variable `SOCRATA_APP_TOKEN`, sent as `X-App-Token`. Never in code,
  never in git.

**The `_Session` Protocol trick.** `fetch_all(session, cfg)` takes a `session` argument instead of creating one.
In production it's `requests.Session()`. In tests it's `FakeSession` (a tiny class with a `.get()` that returns
canned pages). This is **dependency injection**: the code doesn't care *who* answers HTTP, so tests can run
without internet. Remember this pattern — it's how you make network code testable.

### B2. `src/normalize.py` — cleaning + fingerprinting (Silver)

**Job:** turn raw rows into comparable rows.

- **Text normalization.** `norm_text()`: trim, collapse whitespace, uppercase. `"  broadway "` and `"BROADWAY"`
  must be the same thing, or you'll report a "change" when the city just fixed a space.
- **Date parsing.** `parse_date()` tries several formats and returns `YYYY-MM-DD` or `None`. Unparseable dates
  are *counted* (`unparseable_dates`), not fatal.
- **The key.** `build_key()` joins `key_fields` (config). Today that's `uniqueid`. Verified on live data: it's
  the city's own hash of the permit, and the only repeats are exact duplicate rows.
- **The content hash.** `content_hash()` = sha256 of the business fields, serialised as canonical JSON
  (sorted keys, no whitespace). Same fields → same hash, always. This is the **fingerprint** that says
  "changed / unchanged". Note what is *not* in it: Socrata's `:id`, `:updated_at`, `the_geom`. If those were
  included, the city's weekly reload (which changes `:id`) would make every row look modified.
- **Originals are kept.** Each normalized row carries `original` (raw values of the business fields). Nothing is
  lost by normalizing.
- **Quality report.** `QualityReport` counts: exact duplicate rows dropped, key collisions (same key, different
  content — a real problem), null keys (fatal), missing required fields, unparseable dates, schema drift
  (`unexpected_fields` / `absent_fields`). `ok` is false only for things that would make the diff untrustworthy.

**Why "exact duplicate" vs "collision" are treated differently.** Same key + same hash = the source published
the row twice; drop it, count it, carry on. Same key + *different* hash = two different permits share a key,
which means the key is wrong; keep the first, warn loudly. Lumping them together (as the first version did)
hid a benign situation behind a scary warning.

### B3. `src/snapshot.py` — history (still Silver)

**Job:** store each successful pull and keep an index.

- A **snapshot** = the normalized rows of one run, gzipped JSON in `data/snapshots/<run_id>.snapshot.json.gz`.
- The **manifest** (`manifest.json`) = the index: a list of snapshots (with hash, row count, times, quality) and
  a list of *runs* (every attempt, success or failure).
- **Idempotency.** `snapshot_hash()` hashes the whole dataset (sorted `(key, content_hash)` pairs). In
  `persist()`: if the new hash equals the latest snapshot's hash, **no new snapshot file is written** — only a
  run entry with `deduplicated_to`. Run it ten times on unchanged data → one snapshot, ten runs. You proved this
  with runs #2 and #3.

**Why does idempotency matter so much?** Schedulers re-run. Networks retry. Humans double-click. If each run
appended a snapshot regardless, your history would be full of identical copies and "how many times did the
data change?" would be unanswerable.

### B4. `src/compare.py` — the diff

**Job:** previous snapshot vs current → four buckets.

```
added      key in current, not in previous
modified   key in both, content_hash differs   (+ a field-by-field from→to)
unchanged  key in both, same hash
missing    key in previous, not in current
```

Implementation is two dictionaries keyed by `key` and a couple of loops — read `compare()`; it's 25 lines.
`field_diff()` produces `{"work_end_date": {"from": "2026-11-20", "to": "2026-12-05"}}` for modified rows.

**"missing" is a deliberately neutral word.** It means "not in this pull". It does *not* mean "the road
reopened". The permit may have expired, been withdrawn, or the city may have dropped it by mistake. Don't
over-interpret; the README says so too.

### B5. `src/report.py` — what humans (and later n8n) read

**Job:** one JSON + one Markdown per run, plus `latest.report.*` that always points at the newest.

The report carries the three times (source, observation, run id = processing), counts, the diff summary, field
change counts, quality, warnings, and a `status` that is explicitly `"success"` or `"failure"`. A failure report
has `counts.diff = null` — never a fake "0 added" — so nothing downstream can mistake it for a clean run.

### B6. `src/run.py` — the orchestrator

Reads config → sets up a log file per run → fetch (or replay) → volume sanity check → normalize → persist →
compare → report. Exit code `0` on success, `1` on failure. Every failure path goes through `_fail()`, which
records the run in the manifest and writes a failure report.

Two extra modes you'll use a lot:
- `--from-raw <file>`: replay a saved pull. No network. Reproducibility + debugging.
- `--key-check field1 field2`: the audit you ran on day one. Run it again whenever the schema changes.

### B7. `src/adapters/dot_advisory.py` — the scraper

Same pipeline, different front end. Instead of JSON rows it gets HTML and must *invent* rows:

- walks the page's structural tags in document order (`h2` = borough, bare `<strong>` = closure name,
  `<p>` = description, `<h3>/<h4>` = events, `<strong>Label:</strong>` + `<ul>` = street lists);
- a fully-bold paragraph is DOT's own "updated" marker → `updated: true`;
- key = sha1(page | kind | section | name) because the page has no IDs. If DOT rewords a name, you get
  missing + added. That's honest, not a bug;
- a page that parses to zero records raises `ParseError` → failure report, no snapshot — same rule as the API.

Everything after parsing (`snapshot.persist`, `compare.compare`, `report.write_report`) is **reused** unchanged.
That's the payoff of keeping those modules generic: they only need `key`, `content_hash`, `fields`.

---

## Part C — Configuration (`config/`)

`settings.example.json` is the public template (in git). `settings.json` is your private copy (ignored by git).
The code reads `settings.json`. Keep them in sync when the example changes (`Copy-Item … -Force`).

Things you'd realistically change:

| key | when |
|---|---|
| `source.dataset_id` | the city retires `i6b5-j7bu` for the newer Block+Intersection dataset |
| `source.window` | widen/narrow the permit-date range, or `null` for everything |
| `schema.key_fields` | only if `--key-check` ever says `uniqueid` collides with *different* content |
| `schema.business_fields` | a new column appears that should count as "a change" |
| `quality.max_row_delta_pct` | row counts swing a lot and the warning is noise |
| `advisory.pages` | add/remove a DOT page |

Changing `business_fields` changes every content hash → one run of "everything modified". Expected; note it.

---

## Part D — Testing (the part you said you're not good at yet)

### D1. What a test is

A test is a function whose name starts with `test_` that **sets up** some input, **runs** a piece of code, and
**asserts** what should be true afterwards. If an `assert` fails, pytest prints which one and why.

```python
def test_unchanged_rerun(base_rows):
    d = compare.compare(_norm(base_rows), _norm(base_rows))   # run
    assert d.counts == {"added": 0, "modified": 0, "unchanged": 5, "missing": 0}   # check
```

That's it. No magic. Read `tests/test_compare.py` first — it's the simplest file.

### D2. Fixtures

`base_rows`, `modified_rows`, `project` are **fixtures**: functions in `tests/conftest.py` decorated with
`@pytest.fixture`. When a test lists one as a parameter, pytest calls the fixture and passes the result in.
`base_rows` loads `tests/fixtures/rows_base.json` (5 hand-made permits). `modified_rows` is the same 5 with one
changed, one removed, one added — so every bucket of the diff has a known answer.

`tmp_path` is a built-in fixture: a fresh empty folder per test. `project` uses it to create a throwaway
project tree so tests never touch your real `data/`.

### D3. Fakes

`FakeSession` (conftest) and `FakeHtmlSession` (test_dot_advisory) pretend to be the network. They return
pages from a list, and a `script` lets a test say "first answer 503, then 503, then 200" to test retries, or
"always 503" to test the give-up path. This is why the whole suite runs in 0.3 s offline.

### D4. What the tests cover, and why each exists

| file | proves |
|---|---|
| `test_normalize.py` | trimming/case don't change hashes; business-field change does; dates parse; exact dupes drop; collisions warn; null key fatal; schema drift detected |
| `test_compare.py` | first run = all added; rerun = all unchanged; the known modified/missing/added fixture gives exactly 1/2/2/1 |
| `test_ingest.py` | pagination offsets 0/2/4; stops on short page; retries then succeeds; gives up after N; 404 fails fast; non-array body fails; window `$where` built right |
| `test_run.py` | end to end: first run writes snapshot+report+raw+log; rerun is idempotent; modified run diffs vs previous; **failed fetch writes failure report and no snapshot**; blank pull fails; volume swing warns; replay works |
| `test_dot_advisory.py` | parser on realistic HTML (period, sections, closures vs events, updated flag, street lists); keys stable across weeks; nested markup parses identically to flat; layout change fails; two pages keep separate histories; one failing page doesn't block the other |

### D5. How to use tests when debugging

1. **Something broke?** Run `py -m pytest` first. If a test fails, the failure message tells you which
   expectation broke — read the `assert` line and the values pytest prints (`E assert 3 == 5`).
2. **Run one test:** `py -m pytest tests/test_compare.py::test_unchanged_rerun -v`.
3. **See prints/logs:** add `-s` (`py -m pytest -s`), or `--log-cli-level=INFO`.
4. **Found a bug the tests didn't catch?** That's the moment to *write a test that reproduces it* before fixing
   (the `<br />` nesting bug got `test_content_nested_in_wrapper_is_still_parsed`). Then fix until it passes.
   Now the bug can never silently return. This is the single highest-leverage habit in the whole DevOps toolbox.

### D6. What tests *can't* do

They can't tell you the real column is `borough_code` not `borough`, or that your laptop's HTML parser nests
`<br>`. Those come only from running against the real thing on the real machine. Tests protect *logic*;
live runs validate *assumptions*. You need both — and when a live run finds something, turn it into a test.

---

## Part E — Git, enough to be dangerous

### E1. The mental model

Git stores **commits**: snapshots of the whole project, each pointing at its parent. A **branch** (`main`) is
just a name pointing at the latest commit. **origin** is the nickname for the GitHub copy.

Your laptop has: the **working tree** (the files you see), the **staging area** (what will go in the next
commit), and the **history** (commits). Three places, three commands:

```
edit file            → working tree changed      (git status shows M / U)
git add file         → staged                    (ready to commit)
git commit -m "..."  → history                   (snapshot recorded locally)
git push             → origin                    (GitHub now has it)
git pull             → the other direction       (GitHub → your laptop)
```

### E2. What VS Code's Source Control panel shows

- **U** untracked: git has never seen this file. (`git add` to start tracking.)
- **M** modified: tracked file changed since last commit.
- **A** added: staged new file.
- The number badge = files with changes.
- "Commit" button = `git add` (what's staged or, if nothing staged, everything) + `git commit`.
- "Sync" / cloud icon = `git pull` then `git push`.

### E3. `.gitignore`

A list of paths git should pretend don't exist. Ours ignores `config/settings.json` (private), `data/raw/`
(large, re-fetchable), `__pycache__/` (Python's cache), `.venv/`. If a file never shows up in Source Control,
check `.gitignore` first.

### E4. Habits that save you

- **Pull before you start, push when you stop.** The two copies drift otherwise, and merging drift is the
  #1 source of "git is scary".
- **Small commits with a message that says *why*.** Read `git log --oneline` in this repo — each line is an idea.
- **`git status` before every commit.** Make sure you're committing what you think.
- **Never commit secrets.** Tokens go in environment variables / GitHub Secrets. If one slips in, rotating the
  token is the only real fix (history is forever).
- **Undo a local file edit you haven't committed:** `git checkout -- path/to/file`.
- **See what a commit changed:** `git show <hash>`.

### E5. The author line

`git config user.name "Jemma Santiago"` / `user.email "…"` inside the repo folder sets who commits are recorded
as. It's metadata, not authentication — authentication is your GitHub login when you push.

---

## Part F — GitHub Actions (the scheduler)

`.github/workflows/collect.yml` is a recipe GitHub runs on its own Linux machines. Read it top to bottom; each
`- name:` is a step. Things worth understanding:

- **`on: schedule: cron`** — five fields: minute hour day-of-month month day-of-week. `17 11 * * *` = 11:17 UTC
  every day. **`workflow_dispatch`** adds the "Run workflow" button.
- **Each run starts from nothing.** Fresh VM, fresh checkout. That's why history must be committed back (or
  stored elsewhere); anything left on the runner's disk is gone when the job ends.
- **`continue-on-error: true`** on the collector steps — so a failure still reaches the "commit history" step
  (the failure report gets committed) and the final step turns the job red on purpose. A red run in the Actions
  tab is your alert; click it to see the log.
- **`concurrency`** — if yesterday's run is somehow still going, today's waits. Two runs editing the manifest
  at once would corrupt it.
- **Secrets** — `${{ secrets.SOCRATA_APP_TOKEN }}` is injected as an environment variable; it never appears in
  logs or in the repo.
- **Artifacts** — the latest reports are attached to each run as a downloadable zip (Actions → the run → bottom
  of the page), handy when you don't want to dig through commits.

**Reading a failed run:** Actions tab → the red run → click the job → expand the red step. The last ~20 lines
of the log are usually the answer. The same error is also in the committed `data/reports/latest.report.json`.

---

## Part G — How to debug this project on your own

A fixed order of questions. Most problems fall out by step 3.

1. **Did the tests pass?** `py -m pytest`. Red → read the assert. The bug is in logic, not data.
2. **What does the report say?** `Get-Content data\reports\latest.report.md`. `status`, `error`, `warnings`,
   `quality` are written for exactly this moment.
3. **What does the log say?** `logs\<run_id>.log`. Every page, retry, warning, and the stack trace if it crashed.
4. **Is it the data or the code?** Replay the saved raw with `--from-raw`. Same error → code. Different → the
   source changed; look at the raw file.
5. **Look at concrete rows.** The `py -c "… collections.Counter …"` pattern from day one. Group, count, print
   three examples. Don't reason about data you haven't looked at.
6. **Schema drift?** The report's `absent_fields` / `unexpected_fields`. Re-run `--key-check`.
7. **Works for me, not for you?** Library versions (`py -m pip list`), Python version, Windows vs Linux paths.
   The `<br />` bug lived here.
8. **Fixed it?** Write the test that would have caught it. Commit the test and the fix together. Push.

---

## Part H — The layered picture (medallion, roadmap Stage C)

| layer | here | what it is |
|---|---|---|
| Bronze | `data/raw/*.raw.json.gz`, `data/advisory/*/raw/*.html.gz` | the untouched response, append-only, for replay and lineage |
| Silver | `data/snapshots/*.snapshot.json.gz` + `manifest.json` | typed rows, key, content hash, quality flags — the comparable form |
| Gold | *not built yet* | `fact_closure_snapshot`, `fact_snapshot_change`, dims — built from Silver when there's enough history |

The roadmap says to grow Gold only "when history and actual use cases warrant them". With daily runs from now,
you'll have a few weeks of Silver by late November — that's when Stage C starts to make sense.
