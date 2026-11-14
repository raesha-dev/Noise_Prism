# BharatNoise measurement runbook

Run commands from `bharatnoise_api/`. Activate the project environment with
`source ../.venv/bin/activate` on macOS/Linux or
`..\.venv\Scripts\activate` in PowerShell. Do not copy real customer calls,
credentials, transcripts, or third-party audio into the repository.

## Current verified status

- Live Gnani requests and benchmarks run by this work: **0**.
- Completed review sheet: `data/team/voice_group_review_FILLED.csv`, 54 rows,
  zero blockers; all rows are synthetic TTS.
- Team data: 31 Dev / 23 Test clips, split by synthetic voice. Test has not
  been used.
- Dev synthetic mixtures: 62 mixtures from 31 independent Dev source clips.
- GramVaani: 1,132 Dev / 560 Eval manifest rows; no speaker IDs.
- Random GramVaani Dev diagnostic manifest: 60 clips, seed 4187.
- Diagnostic dry run: 180 maximum calls and 26.829 audio minutes. No results
  exist.
- Team mixture conditions C0/C1/C2: each plans 124 calls and 5.755833 audio
  minutes for both models; all three plan 372 calls and 17.267500 minutes.
- If all R0-R4 steps are approved, the total plan is 684 calls / 57.596848
  audio minutes. R4 is conditional; this total is not an authorization to run.
- No benchmark result CSVs, WER/CER, confidence intervals, latency, API
  run-to-run estimate, or improvement finding is available.
- A local controlled-mixture SNR diagnostic measured the stationary estimate
  on the 31 clean-reference Dev clips across five DEMAND noise types and five
  target SNR levels. It was positively biased in the tested cells and remains
  `validated: false`; WADA-SNR remains unimplemented pending equation
  verification. This local diagnostic is not an ASR benchmark.

All commands below are owner-run plans. A dry run is not authorization for a
paid request. Keep the held-out split untouched until the configuration is
frozen, then run it once.

## Credit and call budget gate

Before any benchmark, the owner must check remaining Gnani credits. Measure
the cost per audio minute with the R0 ten-call probe below. Use the actual
charged credits and the actual audio minutes from `plan_budget.py`:

`measured credits per minute = probe credits charged / probe audio minutes`

Keep a 30% reserve: only 70% of remaining credits are available for planned
experiments. Do not proceed if the next step exceeds either that credit budget
or the verified remaining call allowance. Cache hits may reduce calls, but do
not budget on assumed cache hits. Keep requests serial.

## R0: wiring and credit probe (10 calls)

Select ten Dev clips, plan their exact duration, then run one condition only:

```sh
python scripts/select_manifest.py --manifest data/manifests/gramvaani.csv --split dev --output data/manifests/gramvaani_dev.csv
python scripts/sample_dev_manifest.py --manifest data/manifests/gramvaani_dev.csv --output data/manifests/gramvaani_probe10.csv --limit 10 --seed 4187
python scripts/plan_budget.py --manifest data/manifests/gramvaani_probe10.csv --candidates C0 --models v2.5 --runs 1
python run_baseline.py --input data/manifests/gramvaani_probe10.csv --output results/wiring_probe/C0.csv --candidate C0 --model v2.5 --format verbatim --split development --runs 1 --run-id wiring-probe
```

Planned: **10 calls / 1.679431 audio minutes** for the selected seed-4187
sample. Re-run `plan_budget` if the sample changes.
Output: `results/wiring_probe/C0.csv`. Paste back the planned and actual
audio-minutes, calls, credits charged, and aggregate request status counts.
Do not paste transcripts or API credentials. R0 must fit within the 20-call
wiring allowance; do not make a second probe without explicit approval.

## R1: synthetic clean-control Dev baseline (62 calls)

The held-out 23 synthetic clips are excluded. This runs the 31 explicitly
reviewed Dev clean controls once on each model:

```sh
python scripts/import_team_recordings.py --review data/team/voice_group_review_FILLED.csv
python scripts/select_manifest.py --manifest data/manifests/team.csv --split dev --group clean_control --clean-reference-only --output data/manifests/clean_control_dev.csv
python scripts/plan_budget.py --manifest data/manifests/clean_control_dev.csv --candidates C0 --models v2.5 v3.0 --runs 1
python run_baseline.py --input data/manifests/clean_control_dev.csv --output results/team_clean/C0.csv --candidate C0 --models v2.5 v3.0 --format verbatim --split development --runs 1 --run-id team-clean-dev
```

Planned: **62 calls / 2.877917 audio minutes**. Output files are model-specific
under `results/team_clean/`. Paste back only aggregate
status and result summaries. This is synthetic speech, not real telephone
evidence; do not pool it with GramVaani.

## R2: GramVaani C0 diagnosis (60 clips x 3 conditions)

Use the reproducible Dev-only sample. Condition A is v2.5 with the manifest
language, B is v3.0 with that language, and C is v3.0 with `indic-auto`.
Every condition uses `verbatim`.

```sh
python scripts/select_manifest.py --manifest data/manifests/gramvaani.csv --split dev --output data/manifests/gramvaani_dev.csv
python scripts/sample_dev_manifest.py --manifest data/manifests/gramvaani_dev.csv --output data/manifests/gramvaani_dev_sample60.csv --limit 60 --seed 4187
python scripts/diagnose_clips.py --manifest data/manifests/gramvaani_dev_sample60.csv --limit 60 --dry-run
```

The checked dry run planned **180 maximum calls / 26.829 audio minutes**
(60 calls / 8.943 minutes per condition). After R0 and the reserve gate pass:

```sh
python scripts/diagnose_clips.py --manifest data/manifests/gramvaani_dev_sample60.csv --limit 60 --run-id gramvaani-dev-c0 --budget-remaining <verified-calls-available-after-reserve> --confirm-live --output data/diagnosis/c0_models.csv
```

The tool writes separate `c0_models_v25-correct.csv`,
`c0_models_v30-correct.csv`, and `c0_models_v30-auto.csv` files. They include
status, duration, word counts, requested/detected language, and reference
metrics, but do not store hypotheses. Paste back aggregate EMPTY, TOO_SHORT,
ERROR, TRUNCATED, language-mismatch, and text-returned counts by condition,
plus paired WER/CER summaries. Do not paste raw references or transcripts.

## R3: repeat condition A (60 calls)

Run condition A once more with a new run ID and output path to measure
recognizer variation:

```sh
python scripts/plan_budget.py --manifest data/manifests/gramvaani_dev_sample60.csv --candidates C0 --models v2.5 --runs 1
python run_baseline.py --input data/manifests/gramvaani_dev_sample60.csv --output results/repeat/C0_A_repeat.csv --candidate C0 --model v2.5 --format verbatim --split development --runs 1 --run-id gramvaani-dev-A-repeat
```

Planned: **60 calls / 8.943 audio minutes** for the current sample. Re-plan if
the sample changes.
Compare against condition A's corresponding result rows; keep this repeat
separate from candidate aggregation.

## R4: synthetic Dev mixture candidates (conditional)

Only proceed if R2 supports measuring these candidates and the credit gate
passes. Each candidate uses 62 mixtures / 31 independent source clips; both
models plan 124 calls / 5.755833 audio minutes per candidate. Three candidates
plan 372 calls / 17.267500 minutes. Language-specific results are
`UNDERPOWERED` below 20 independent source clips.

```sh
python scripts/build_eval_set.py --manifest data/manifests/clean_control_dev.csv --noise-dir data/noise --output-dir data/eval/dev --mixtures-per-clip 2
python scripts/make_candidate_manifests.py --manifest data/eval/dev/dev.csv --output-dir data/candidates/dev --candidates C0 C1 C2
python scripts/plan_budget.py --manifest data/candidates/dev/C0.csv --candidates C0 --models v2.5 v3.0 --runs 1
python scripts/plan_budget.py --manifest data/candidates/dev/C1.csv --candidates C1 --models v2.5 v3.0 --runs 1
python scripts/plan_budget.py --manifest data/candidates/dev/C2.csv --candidates C2 --models v2.5 v3.0 --runs 1
python run_baseline.py --input data/candidates/dev/C0.csv --output results/dev/C0.csv --candidate C0 --models v2.5 v3.0 --format verbatim --split development --runs 1 --run-id team-mixture-dev
python run_baseline.py --input data/candidates/dev/C1.csv --output results/dev/C1.csv --candidate C1 --models v2.5 v3.0 --format verbatim --split development --runs 1 --run-id team-mixture-dev
python run_baseline.py --input data/candidates/dev/C2.csv --output results/dev/C2.csv --candidate C2 --models v2.5 v3.0 --format verbatim --split development --runs 1 --run-id team-mixture-dev
```

Use separate results for any condition/model that is not selected above.
Never add test rows to these commands. C2/C2B/C2C settings remain unfitted
unless chosen using Dev-only paired evidence.

## R5: offline analysis only

No diagnosis results or condition files are present, so fallback simulation
and failure analysis have not been run. After the owner supplies the three
condition summaries, create `docs/diagnosis.md` from those files only. A
fallback may use EMPTY, TOO_SHORT, or LANGUAGE_MISMATCH symptoms; references
may score the selected output but may not determine which condition is chosen.
Report API-call count and cost with each simulated policy.

## R6-R7: freeze and one-time held-out run (owner only)

Do not freeze or run held-out data before Dev evidence, router settings, and
selected parameters are finalized. The current route is explicitly baseline
only. If Dev evidence does not justify a change, the owner can freeze C0
without claiming the router is fitted; the frozen file records that status.
If a non-C0 route is selected, first validate the estimator and fit the router
on Dev data. Then freeze:

```sh
python scripts/freeze_config.py --observation-lambda none
```

Only after a matching frozen hash exists, the owner may select held-out rows.
For a baseline-only GramVaani Eval measurement:

```sh
python scripts/select_manifest.py --manifest data/manifests/gramvaani.csv --split test --output data/manifests/gramvaani_eval.csv
python scripts/plan_budget.py --manifest data/manifests/gramvaani_eval.csv --candidates C0 --models v2.5 v3.0 --runs 1
python scripts/run_frozen_test.py --candidate-manifest C0=data/manifests/gramvaani_eval.csv --models v2.5 v3.0 --run-id held-out-c0-final --confirm-calls <exact-plan-budget-call-count>
```

For any additional candidate, build a matching held-out candidate manifest
after freeze, plan every candidate/model combination, and use the exact summed
call count. Invoke the frozen test runner once. Do not retry a partial or
failed held-out run; its state is consumed. The held-out audio, transcripts,
and per-clip IDs must remain local.

## R8: aggregate and publish measured summaries

Aggregate one measured run per candidate/model in a split-specific directory;
never include mock outputs or repeat-run CSVs in this directory:

```sh
python scripts/aggregate_results.py --results-dir results/dev --n-boot 2000 --seed 0
python scripts/make_results_md.py --results-dir results/dev
```

`make_results_md.py` renders `docs/RESULTS.md` and the marked README results
section from aggregate CSV files only. Missing results are shown as **not
measured**; underpowered comparisons remain explicitly suppressed. Keep
GramVaani real-telephone results and synthetic speech results separate.

## Unfitted/unverified values and deferred work

The short-output words-per-second threshold, router thresholds, stationary
noise estimate, suppression settings, and spectral-candidate parameters are
not fitted/validated for routing. The simplified spectral implementation's
fidelity to cited equations is unverified. WADA-SNR is unimplemented.
Chunking, language fallback, and recognizer-side changes remain undecided
until the C0 diagnosis. No held-out score or WER improvement is claimed.
