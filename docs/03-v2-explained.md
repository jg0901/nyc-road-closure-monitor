# V2 explained — scheduled, observable, for someone new to CI/CD

V1 was "it works when I run it". V2 is "it runs without me, I can see what it did, and it tells me when it
fails". That is the whole of operations in one sentence, and this doc walks through how
`.github/workflows/collect.yml` delivers each of those three things.

Read Part A for the concepts, Part B with the YAML file open next to it, Part C before you press the button,
Part D when something goes wrong.

---

## Part A — Concepts

### A1. What GitHub Actions is

GitHub will run a list of shell commands for you on a computer it owns, whenever a trigger you choose fires.
That's it. The list of commands is a **workflow** (a YAML file in `.github/workflows/`). One execution of it
is a **run**. A run contains one or more **jobs**; a job is a sequence of **steps** on one machine (the
**runner**). Our workflow has one job, `collect`, with nine steps.

You've already met the idea under a different name: a cron job on a server, or a scheduled task in Windows.
The difference is that the machine is rented per run and thrown away afterwards.

### A2. The runner is disposable — this changes everything

Every run starts on a **brand-new Linux VM**: empty disk, nothing installed, no files from last time. When the
job ends the VM is deleted. Consequences:

- The repo must be *checked out* (downloaded) at the start of every run (`actions/checkout`).
- Python and libraries must be *installed* every run (`setup-python`, `pip install`).
- Anything the run produced — our snapshots, manifests, reports — **vanishes with the VM** unless the run puts
  it somewhere persistent before it ends.

That last point is the roadmap's "never rely on runner-local SQLite" warning. We chose to persist by
**committing the files back into the repo** (git is persistent storage GitHub already gives us). Later, when
history gets big, the same step would upload to object storage (S3 / Supabase) instead. Nothing else changes.

### A3. Triggers

Two in our file:

- `schedule` with a **cron** expression: `17 11 * * *` → minute 17, hour 11, every day-of-month, every month,
  every weekday → **11:17 UTC daily** = 19:17 Manila. GitHub cron is **always UTC**; you convert.
  The odd minute is deliberate: thousands of workflows fire at `:00`, and GitHub delays those.
- `workflow_dispatch`: puts a **Run workflow** button in the Actions tab. For testing and for "I don't want to
  wait until 19:17".

Two cron facts that surprise people: (1) scheduled runs can start **several minutes late** under load — it's
"no earlier than", not "exactly"; (2) GitHub **pauses scheduled workflows on public repos after 60 days with no
commits**. Our runs commit daily, so this never triggers, but if you ever stop the commit step, remember this.

### A4. Permissions and secrets

- `permissions: contents: write` — by default the token a run gets can only *read* the repo. We need to push,
  so we ask for write. Principle of least privilege: ask for exactly what the job needs.
- `${{ secrets.SOCRATA_APP_TOKEN }}` — a value you store in **Settings → Secrets and variables → Actions**. It
  is injected into the step as an environment variable, masked (`***`) in logs, never written to the repo.
  If the secret doesn't exist, the variable is simply empty and our code runs without a token (throttled but
  working). Secrets are **not available** to runs triggered by pull requests from forks — irrelevant for a
  solo repo, good to know later.

### A5. Why history lives in git (for now)

Pros: free, already there, every change to `manifest.json` is itself a commit with a timestamp, you can `git log
-- data/snapshots/manifest.json` and see the whole run history, and cloning the repo gives anyone the full data.

Cons: git is bad at large or frequently-changing binary files. Our numbers: API snapshot ≈ 150 KB gzipped and
only written when the data changes (weekly), weekend advisory ≈ 20 KB, reports a few KB, logs a few KB.
≈ 1 MB/month. Fine for a year. The README says to revisit at ~100 MB.

What stays out: `data/raw/` for the API (every row carries street geometry; ~1 MB per pull, daily → too much).
Raw is re-fetchable, and the snapshot + manifest are what history needs. Advisory raw HTML (8–40 KB) is kept
because the parser is the fragile part there and you'll want the exact bytes to replay.

### A6. What "observable" means here

Three places to look, cheapest first:

1. **The Actions tab** — green ✓ or red ✗ per run. Red = a collector failed. That's the alert, no email needed
   (you can turn on email notifications for failed workflows in your GitHub settings).
2. **The commit** — each run leaves a commit `collect: <time> api=success advisory=success` on `main`. The
   outcome is in the message; `git log --oneline` is a run history.
3. **The files** — `data/reports/latest.report.md`, `data/advisory/weekly/reports/latest.report.md`, the
   manifests, and `logs/`. Committed, so `git pull` brings them to your laptop. Also attached to each run as a
   downloadable **artifact** (zip at the bottom of the run page).

---

## Part B — The workflow file, step by step

Open `.github/workflows/collect.yml`.

```yaml
name: collect
```
The name shown in the Actions tab.

```yaml
on:
  schedule:
    - cron: "17 11 * * *"
  workflow_dispatch:
```
Triggers (A3).

```yaml
permissions:
  contents: write
```
Allow pushing (A4).

```yaml
concurrency:
  group: collect
  cancel-in-progress: false
```
Only one run of this workflow at a time. If a run is still going when the next trigger fires, the new one
**waits** (not cancels). Two runs committing to `manifest.json` simultaneously would produce a merge conflict
and one would fail the push. This is the "deterministic state transitions" line from the roadmap, in one stanza.

```yaml
jobs:
  collect:
    runs-on: ubuntu-latest
```
One job, on GitHub's standard Linux image.

**Step 1 — checkout.** `actions/checkout@v4` clones the repo into the runner's working directory. Without this
the runner has nothing. The `@v4` pins the action's major version so an upstream change can't break us silently.

**Step 2 — Python.** `actions/setup-python@v5` with `python-version: "3.12"`. We pin 3.12 (not "latest") so the
runner's behaviour is predictable — you already saw a 3.13-specific HTML parsing difference on your laptop.

**Step 3 — dependencies.** `pip install -r requirements.txt`. Same file you used locally; same libraries.

**Step 4 — tests.** `python -m pytest`. If the tests fail, the job stops here and goes red *before touching the
API*. Broken code never writes history. This is the cheapest guardrail in the file.

**Step 5 — config.** `cp config/settings.example.json config/settings.json`. The runner has no private
`settings.json` (it's gitignored), so the example *is* the config in production. Hence: keep the example
correct, always.

**Step 6 — API collector.**
```yaml
id: api
env:
  SOCRATA_APP_TOKEN: ${{ secrets.SOCRATA_APP_TOKEN }}
run: python -m src.run --config config/settings.json
continue-on-error: true
```
- `id:` lets later steps refer to this step's outcome (`steps.api.outcome`).
- `env:` injects the secret for this step only.
- `continue-on-error: true` — **normally a non-zero exit code stops the job immediately**. We override that,
  because if the collector fails we still want steps 8 and 9 to run: commit the failure report, then fail the
  job *on purpose*. Without this, a failed fetch would leave no trace in the repo.

**Step 7 — scrapers.** Same shape, `id: advisory`. Runs weekly + weekend; the adapter itself continues past a
failing page and exits 1 if any failed.

**Step 8 — commit history.**
```bash
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data logs
if git diff --cached --quiet; then echo "nothing to commit"; else
  git commit -m "collect: $(date -u +%Y-%m-%dT%H:%M:%SZ) api=${{ steps.api.outcome }} advisory=${{ steps.advisory.outcome }}"
  git pull --rebase origin main
  git push origin HEAD:main
fi
```
- The commit author is the bot, not you. Those commits are machine-made; attributing them to a human would be
  misleading in `git blame`. (Your own commits stay yours.)
- `git add data logs` stages everything the run produced (minus what `.gitignore` excludes).
- `git diff --cached --quiet` → "is anything actually staged?" If the dataset was unchanged *and* nothing else
  moved, there'd be nothing — in practice the manifest's `runs` list always grows, so there's always a commit.
- The commit message carries both outcomes: `api=success advisory=failure` is readable in `git log` forever.
- `git pull --rebase` before `push`: if you pushed a code change between checkout and now, this replays the
  bot's commit on top of yours instead of failing. `HEAD:main` pushes the current commit to `main`.

**Step 9 — artifact.** `actions/upload-artifact@v4` attaches the `latest.report.*` files to the run page.
`if: always()` means it runs even if an earlier step failed — a failure report is exactly when you want this.
`if-no-files-found: ignore` so a run that died before producing any report doesn't fail *again* here.

**Step 10 — fail on purpose.**
```yaml
if: steps.api.outcome == 'failure' || steps.advisory.outcome == 'failure'
run: exit 1
```
Only now, after the evidence is committed and uploaded, do we turn the run red. This is the roadmap's "a failed
fetch must not be treated as an empty dataset; alert/report must expose failure" — the report exposes it, the
red run alerts.

---

## Part C — Operating it

### C1. First run (do this once)

1. `git pull` locally, and delete your local untracked history so the runner's commit becomes the baseline:
   `Remove-Item -Recurse -Force data\snapshots\manifest.json, data\advisory, logs`.
2. (Optional) add the secret: repo **Settings → Secrets and variables → Actions → New repository secret** →
   name `SOCRATA_APP_TOKEN`, value = the token from https://data.cityofnewyork.us/profile/app_tokens.
3. **Actions** tab → **collect** → **Run workflow** → **Run workflow**. Refresh after ~10 s; a run appears.
4. Click it → click the `collect` job → watch steps turn green. ~2 min.
5. Back on the repo's Code tab: a new commit by `github-actions[bot]` on `main`. `git pull` locally.

### C2. The failure demo (V2 definition of done, line 2)

You need to *see* the red path once so you trust it.

1. Edit `config/settings.example.json`: change `"dataset_id": "i6b5-j7bu"` to `"i6b5-xxxx"`. Commit, push.
2. Run workflow. Expect: step 6 red but the job continues, step 8 commits `api=failure`, step 9 uploads the
   failure report, step 10 turns the run **red**.
3. Open `data/reports/latest.report.md` on GitHub: `# Run … — FAILURE`, with the HTTP 404 in `error`.
   Open the manifest: the last run has `"status": "failure"` and **no new snapshot**.
4. Revert the dataset id. Commit, push. Next run is green again; the failure stays in history as evidence.

### C3. Gate 2 checklist

- [ ] one `workflow_dispatch` run green
- [ ] one `schedule` run green (tomorrow 19:17 Manila; check the Actions tab after ~19:30)
- [ ] one forced failure: red run, failure report committed, no snapshot written
- [ ] README "Sample output" filled from the runner's `latest.report.md`

When all four are ticked, the roadmap says: V2 is done, move on. Don't polish.

### C4. Day-to-day

Nothing. Glance at the Actions tab every few days, or turn on failure emails (GitHub → Settings →
Notifications → Actions → "Send notifications for failed workflows only"). `git pull` when you want the data
locally. The weekly/weekend pages roll on Sat/Fri; the API reloads on Mondays; the diffs will be boring most
days and interesting on those.

---

## Part D — When a run goes red

Click the run → the job → the red step. Read the last 20 lines. Then match:

| symptom | likely cause | fix |
|---|---|---|
| Step 4 `pytest` red | code bug pushed | run tests locally, fix, push; the next run heals |
| Step 6 red, `HTTP 404` | wrong `dataset_id` or the city retired the dataset | check the dataset page; update config |
| Step 6 red, `HTTP 400` | `$where` names a column that no longer exists | schema drift — re-run `--key-check`, fix `window.*_field` |
| Step 6 red, `gave up after 4 attempts … 503` | Socrata outage or throttling | add/confirm the app token; otherwise just wait for tomorrow's run |
| Step 6 red, `only 0 rows fetched` | window excludes everything, or API returned empty | check `window` dates; check the dataset page shows rows |
| Step 7 red, `ParseError: no closure/event records parsed` | DOT changed the page layout | download the artifact's raw html, fix parser, add a fixture test, push |
| Step 7 red for one page only | that page is down / restructured | the other page still committed; fix at leisure |
| Step 8 red, `permission denied` on push | `permissions: contents: write` missing, or repo settings block Actions from writing | Settings → Actions → General → Workflow permissions → Read and write |
| Step 8 red, rebase conflict | you and the bot edited the same committed file | `git pull` locally, resolve, push; re-run workflow |
| Scheduled run never appeared | cron delay (wait 15 min), or workflow disabled after inactivity | Actions tab → enable workflow; or dispatch manually |
| Run green but no new commit | nothing changed *and* manifest unchanged — shouldn't happen | check step 8 log says "nothing to commit"; inspect why `runs` didn't grow |

Debug locally with the exact inputs: download the run artifact (reports) or `git pull` (manifests, logs,
advisory raw HTML), then `--from-raw` / `--from-html` to replay.

---

## Part E — What V2 is *not* (yet)

- **Not n8n.** That's Stage B (Nov 16+): n8n receives `latest.report.json` via a webhook and routes failures
  vs changes to Telegram/email. The commented-out `curl` at the end of the workflow is where it plugs in.
  The division of labour from the roadmap: Python owns fetching/diffing/state; n8n owns delivery and routing.
- **Not object storage.** Git is the store until size says otherwise.
- **Not Databricks.** Silver history accumulates in git; Gold tables come when there's enough of it to be
  worth modelling (roadmap Stage C, late Nov–Dec).
- **Not a dashboard.** Reports are Markdown. A per-month DQ dashboard is a later, separate deliverable.

The point of V2 is a boring, reliable loop you can stop thinking about. If the Actions tab is green for two
weeks straight, V2 did its job.
