# What happened today (Nov 5–6, 2026) — step by step

This is the story of how this repo went from a PDF roadmap to a working, scheduled collector in one evening.
It explains **every command you ran, why you ran it, and what it did**. Read it top to bottom once; later,
use it as a reference when you forget "why did I type that again?".

---

## 0. The cast of tools (what each thing is)

| Tool | What it is | Why it exists in this project |
|---|---|---|
| **Git** | A program that records snapshots ("commits") of a folder over time. Lives on your computer. | So you can see what changed, undo mistakes, and so two copies of the project (your laptop, GitHub) can be kept in sync. |
| **GitHub** | A website that hosts git repositories ("repos") and runs automation (GitHub Actions). | Backup, portfolio, and the place where the scheduled runs happen. |
| **VS Code** | A text editor with a terminal and git buttons built in. | Where you edit files and run commands without switching windows. |
| **PowerShell** | The command-line shell on Windows. The terminal inside VS Code runs it. | Commands like `py -m pytest` are typed here. |
| **Python** (`py`) | The programming language the project is written in. `py` is the Windows launcher for it. | Runs the code. |
| **pip** | Python's package installer. | Downloads libraries the project depends on (`requests`, `beautifulsoup4`, `pytest`). |
| **pytest** | A test runner. Finds files named `test_*.py`, runs each function named `test_*`, reports pass/fail. | Proves the code does what we think it does, without needing the internet. |
| **requests** | Python library for HTTP (talking to websites/APIs). | Fetches the NYC Open Data API and the DOT advisory pages. |
| **beautifulsoup4** (bs4) | Python library that turns HTML into a tree you can walk. | Parses the advisory pages. |
| **Socrata / SODA** | The platform NYC Open Data runs on, and its API style. | The construction-closure dataset is served by it. |
| **GitHub Actions** | GitHub's built-in automation: "run these commands on a fresh Linux machine on a schedule". | The daily collection run (V2). |

---

## 1. Planning (before any code)

**What happened:** You shared `NYC_Project_Roadmap.pdf`. It defines V1 (local collector + snapshot diff) and V2
(scheduled, observable), and — importantly — says what *not* to build this week.

**Decisions made from it:**
- Source = the NYC Open Data **API** (Socrata dataset `i6b5-j7bu`), not web scraping, because the API gives
  structured rows with IDs. Scraping was added later as a *separate* adapter.
- Project layout = exactly what the roadmap's Workstream 02 table suggests (`src/ingest.py`, `normalize.py`, …).
- The key field (`uniqueid`) was flagged as "verify on first pull" — which turned out to matter.

> **Why plan first?** The roadmap already answered the expensive questions (which source, what counts as done).
> Everything after this is execution, not invention.

---

## 2. The scaffold (code written before any data existed)

**What happened:** The whole V1 skeleton was written against *synthetic* data (small hand-made rows in
`tests/fixtures/`), with 29 tests, before touching the real API.

**Why this order?** The structure (pagination, retries, hashing, diffing, idempotency) doesn't depend on what the
real columns are called. Only one decision did — the key — and that was made a config value so it could change
without touching code. Writing the skeleton first meant Friday's real work was *verification*, not boilerplate.

**What was produced:** a zip with `src/`, `tests/`, `config/settings.example.json`, `README.md`.

---

## 3. Putting it on GitHub

**What happened:** You had already created an empty repo `jg0901/nyc-road-closure-monitor` (one commit: the
README GitHub makes for you). The scaffold was added on top as three commits, following the roadmap's suggested
"commit 1 / 2 / 3" sequence:

1. structure + config + README
2. ingestion, normalize, snapshot, report, run
3. diff + tests

**Why three commits and not one?** A commit is a *unit of meaning*. "Add structure", "add ingestion", "add diff"
are three ideas. Later, `git log` reads like a changelog, and if one idea was wrong you can revert just that one.

**Why "commit as Jemma"?** The commit author is metadata recorded in git. It's your repo and your portfolio;
the author line is set with `git config user.name / user.email`.

---

## 4. Cloning to your laptop

**Command you (effectively) ran:**

```powershell
git clone https://github.com/jg0901/nyc-road-closure-monitor
```

**What it does:** downloads the entire repo — every file *and* every commit — into a new folder
`nyc-road-closure-monitor` on your computer, and remembers where it came from (`origin`).

**Mental model:** there are now **two copies** of the project. GitHub's copy (`origin/main`) and yours (`main`).
They only sync when you tell them to: `git pull` (GitHub → you) and `git push` (you → GitHub).

**VS Code:** File → Open Folder → pick `nyc-road-closure-monitor`. The Source Control icon (the branchy one on the
left) now shows git status for this folder. The terminal (``Ctrl+` ``) opens *inside* this folder, which is why
commands like `py -m pytest` find the project.

---

## 5. Setting up Python for the project

**What was attempted:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**What each line means:**
- `python -m venv .venv` → create a **virtual environment**: a private, project-only copy of Python inside a
  folder named `.venv`. Libraries installed into it don't affect other projects.
- `.\.venv\Scripts\Activate.ps1` → "switch this terminal to use the `.venv` Python". The prompt gets a `(.venv)`
  prefix when it works.
- `pip install -r requirements.txt` → install the libraries listed in `requirements.txt`.

**What actually happened on your machine:** `python` wasn't on PATH (Windows's "App execution alias" intercepts
it), so `python` failed and the venv was never created/activated. `py` (the Windows Python launcher) worked,
and `py -m pip install -r requirements.txt` installed the libraries into your **global** Python 3.13 instead.

**Is that a problem?** Not today. It's untidy: every project on your laptop now shares one set of libraries. Fix
later with `py -m venv .venv` then `.\.venv\Scripts\Activate.ps1` then `py -m pip install -r requirements.txt`.

**Three PowerShell gotchas you hit:**
- `&&` doesn't chain commands in Windows PowerShell 5. Run commands one per line.
- Scripts in the current folder need `.\` in front: `.\.venv\Scripts\Activate.ps1`.
- `python` vs `py`: on your machine use `py`. `py -m something` means "run module *something* with this Python".

---

## 6. Running the tests

```powershell
py -m pytest
```

**Output:** `46 passed` (later 53). pytest found every `tests/test_*.py`, ran every `def test_...()` inside, and
counted passes. **No internet used** — the tests feed the code fake rows from `tests/fixtures/` and a fake
"session" that pretends to be the API.

**Why run tests before anything else?** It answers "is the code I just downloaded healthy *on my machine*?"
If this is red, nothing else is worth trying. (It also caught nothing today — but it *would* have if, say, your
Python version lacked something the code needs.)

---

## 7. Making your config

```powershell
Copy-Item config\settings.example.json config\settings.json
```

**Why two files?** `settings.example.json` is committed to git — the public template. `settings.json` is in
`.gitignore` — your private copy, where tokens or local tweaks could go without ever being pushed by accident.
The code reads `settings.json`. (Right now they're identical; the separation is a habit that pays off later.)

---

## 8. The key audit — the first real decision

```powershell
py -m src.run --config config/settings.json --key-check uniqueid segmentid
```

**What it did:** fetched the whole dataset (3,689 rows, within the Nov 1 – Jan 31 window) and counted, for each
candidate field, rows vs distinct values:

```
uniqueid     3689  3689  3530  False
segmentid    3689  3689  1824  False
```

**What it told us:** `uniqueid` is *not* unique (3,530 distinct in 3,689 rows). Also — quietly — that the window
filter was accepted, meaning `work_start_date` / `work_end_date` are real column names.

**Why this matters:** the whole change-detection design hangs on "one key = one permit". If the key is wrong, every
diff is wrong. This single command is the most important thing you ran today.

---

## 9. First real run

```powershell
py -m src.run --config config/settings.json
```

**What happened, line by line in the log:**
- `window filter: …` → the SODA `$where` sent to the API.
- `page 1: 1000 rows … page 4: 689 rows` → pagination; it stopped when a page came back short.
- `HTTP 503 (retryable)` → the API hiccuped once; the code waited and retried; the retry worked. **This is the
  retry logic proving itself on real traffic.**
- `raw saved: data\raw\…raw.json.gz` → the exact response was stored (Bronze layer) before anything else.
- `configured fields absent from data: ['borough']` → schema drift check: our config named a column that doesn't
  exist (it's `borough_code`).
- `140 keys occur more than once` → the duplicate problem from step 8, now visible in the pipeline.
- `snapshot written` / `report written` / `success: {'added': 3530, …}` → first snapshot. Everything is "added"
  because there was nothing to compare to.

---

## 10. Diagnosing the duplicates

```powershell
py -c "import gzip,json,glob,collections; …"
```

**What it did:** opened the raw file from step 9, grouped rows by `uniqueid`, and printed three duplicate groups
side by side, plus the real column list.

**What we learned:**
- The duplicates are **exact copies** — every business field identical; only Socrata's internal `:id` differs.
  The city publishes some permits 2–4 times. Dropping duplicates is correct.
- Real columns: `uniqueid, segmentid, oft, onstreetname, fromstreetname, tostreetname, borough_code,
  work_start_date, work_end_date, purpose, the_geom`.
- Every row has the same `:created_at` → the city **truncates and reloads** weekly, so `:id` isn't stable
  across weeks. Only `uniqueid` + our content hash carry history.

> **Debugging pattern to remember:** when a number looks wrong, don't guess — pull a few concrete examples and
> *look* at them. One `py -c` with `collections.Counter` answered a question no amount of reasoning would have.

---

## 11. Fixing config + code, then re-running

**Pushed from here:** `borough` → `borough_code`, add `oft`; normalize now separates "exact duplicate (drop,
count)" from "same key, different content (keep first, warn)".

**You ran:**

```powershell
git pull
Copy-Item config\settings.example.json config\settings.json -Force
py -m src.run --config config/settings.json
py -m src.run --config config/settings.json
Get-Content data\snapshots\manifest.json
```

- `git pull` → bring the fix from GitHub to your laptop.
- `Copy-Item … -Force` → overwrite your `settings.json` with the corrected template.
- run #1 → `dropped 159 exact duplicate rows`, no warnings, **new snapshot** (3,530 rows). Diff said
  `modified: 3530` — because the content hash now includes `borough_code`/`oft`, every hash changed once.
  A one-time artifact of the schema fix, not real change.
- run #2 → `dataset unchanged … no new snapshot written`, `unchanged: 3530`. **Idempotency proven**: same input,
  no duplicate history.
- manifest → 2 snapshots, 3 runs, the third with `deduplicated_to`. That is V1's definition of done.

---

## 12. The scraper — and a bug only your machine could find

```powershell
py -m src.adapters.dot_advisory --config config/settings.json
```

**Expected:** 14 closures + 6 events (counted in the browser earlier). **Got:** 5 + 5, stopping mid-page.

**Diagnosis path (three commands you ran):**
1. Confirmed the saved HTML on your disk was byte-identical to what the browser saw → not a download problem.
2. Reproduced the exact tag sequence on my side → parsed fine → not the HTML's fault either.
3. Asked bs4 on *your* machine what the tree looked like: everything after a `<br />` was **nested inside the
   `<br>`**. Your Python 3.13 + bs4 4.14 build treats `<br />` as a container; mine treats it as void.

**Fix:** the parser now walks all descendants in document order instead of trusting "direct children". Tests
added that wrap content in extra tags to make sure nesting never matters again.

**Verification you ran:** `--from-html <saved file>` replayed the exact bytes that had failed → `14 + 6`. No
network needed. **That's why raw files are kept.**

> **Lesson:** "works on my machine" is a real category of bug. Different library versions build different trees
> from the same HTML. Tests with fixtures catch logic errors; only a run on the target machine catches these.

---

## 13. Adding the weekend page

You asked about the other advisory pages. Checked both in the browser:
- **Weekend** advisory: same grammar as weekly, ~210 closures → added as a second page with its own history.
- **Special** advisories: prose only, nothing to parse → skipped (documented in README).

You ran `git pull`, re-copied config, deleted the old `data\advisory` (layout changed to per-page folders), and
ran the scraper: `weekly … 14 + 6`, `weekend … 210 + 1`.

---

## 14. The three untracked `manifest.json` files in VS Code

The Source Control panel showed three files with a green **U** (untracked = git has never seen them). You asked
"push this?" — answer was **no, not yet**: they contained your local Windows paths, and V2 was about to decide
where history lives. The runner's first commit becomes the clean baseline.

---

## 15. V2: the scheduled workflow

**Pushed:** `.github/workflows/collect.yml`. Every day at 11:17 UTC (19:17 Manila), and whenever you press
"Run workflow", GitHub:

1. checks out the repo on a fresh Linux machine,
2. installs Python 3.12 + dependencies,
3. runs the tests,
4. runs the API collector, then the two scrapers (each continues even if the other failed),
5. commits `data/` and `logs/` back to `main` as `github-actions[bot]`,
6. uploads the latest reports as a downloadable artifact,
7. **marks the run failed if any collector failed** — so a red run in the Actions tab *is* the alert.

**What you do:** add the `SOCRATA_APP_TOKEN` secret (optional), press Run workflow once, and let the cron do
the second run tomorrow evening. Then force one failure on purpose (wrong `dataset_id`) to see the red path.

---

## 16. Commands cheat-sheet (everything from today, in order)

```powershell
# one-time setup
git clone https://github.com/jg0901/nyc-road-closure-monitor
cd nyc-road-closure-monitor
py -m pip install -r requirements.txt
Copy-Item config\settings.example.json config\settings.json

# every time you sit down
git pull                                   # get the latest from GitHub
py -m pytest                               # is the code healthy?

# the collector
py -m src.run --config config/settings.json
py -m src.run --config config/settings.json --key-check uniqueid segmentid
py -m src.run --config config/settings.json --from-raw data\raw\<run_id>.raw.json.gz   # replay, no network

# the scrapers
py -m src.adapters.dot_advisory --config config/settings.json
py -m src.adapters.dot_advisory --config config/settings.json --page weekend
py -m src.adapters.dot_advisory --config config/settings.json --page weekly --from-html data\advisory\weekly\raw\<run_id>.advisory.html.gz

# looking at results
Get-Content data\reports\latest.report.md
Get-Content data\snapshots\manifest.json
Get-Content data\advisory\weekend\reports\latest.report.md | Select-Object -First 30

# git, when you change something yourself
git status                                 # what's changed?
git add <file>                             # stage it
git commit -m "what and why"               # record it
git push                                   # send to GitHub
```
