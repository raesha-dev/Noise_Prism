# BharatNoise

BharatNoise is a React/TypeScript workstation and FastAPI proxy for experimenting
with speech preprocessing on noisy 8 kHz telephone audio. It can run against the
local mock without Gnani credentials; mock transcripts are explicitly labeled,
and mock runs are not benchmark results.

No measured benchmark results are included in this repository. The API only serves
results generated from CSVs in `bharatnoise_api/results/`; it never serves mock
outputs from `results_mock/`.

## Quickstart

Requirements: Python 3.10–3.14, Node.js 20.19+ (or 22.12+) with npm, and Make.

From the repository root, create a project-local Python environment and install
the core backend and frontend dependencies:

```sh
make setup
```

For directly invoking Python tools, activate the environment after setup:

```sh
source .venv/bin/activate
```

In PowerShell, use `.venv\Scripts\Activate.ps1`.

Start the mock Gnani service, backend, and frontend together:

```sh
make dev-mock
```

Open <http://127.0.0.1:8080>. The UI displays a persistent mock warning; mock
transcripts are placeholders, not Prisma output, and mock mode hides WER/CER.
Press Ctrl-C in the terminal to stop all three services.

Run the local checks with:

```sh
make test
make lint
make e2e
```

The backend can also run without any Gnani key:

```sh
.venv/bin/python -m uvicorn server:app --workers 1
```

On Windows, use `.venv\Scripts\python.exe -m uvicorn server:app --workers 1`.
Without a key, health/config report `gnani_mode: "unconfigured"`; transcription
jobs fail with `GNANI_KEY_MISSING`, and the live proxy refuses the connection.

## Go live with real credentials

1. Copy `.env.example` to `.env` in the repository root and set `GNANI_API_KEY`.
   Clear any mock URLs and set the official endpoint values:
   `GNANI_BASE_URL=https://api.vachana.ai` and
   `GNANI_WS_URL=wss://api.vachana.ai/stt/v3/stream`. The backend also loads
   `bharatnoise_api/.env`; real environment variables take precedence.
2. Verify the configured REST and WebSocket endpoints using synthetic silence:

   ```sh
   make preflight
   ```

   The script exits with code 3 and prints `NO KEY: nothing was verified` when
   no key is configured; `make` reports that failed target as exit code 2. The
   script exits 0 only when the configured checks pass.
3. Start the workstation with `make dev`, then upload an authorized real clip in
   the UI. Do not use customer calls or personal data without authorization.
4. Before spending credits, validate the manifest and estimate the call count:

   ```sh
   make plan MANIFEST=data/probe.csv CANDIDATES=C0 MODELS=v2.5 RUNS=10
   ```

   Create `data/probe.csv` with one eligible authorized clip. Manifest paths
   passed to `make plan` are relative to `bharatnoise_api/`; this plan should
   report the exact ten calls intended for the probe.
5. After reviewing the plan, use a manifest containing exactly one eligible clip
   for a ten-call credit probe:

   ```sh
   cd bharatnoise_api
   python run_baseline.py --input data/probe.csv --output results/probe.csv \
     --candidate C0 --model v2.5 --runs 10 --run-id credit-probe
   cd ..
   ```

   `data/probe.csv` must be a valid manifest with one audio row, and its clip must
   be at most 30 seconds. This command makes real API calls when the official
   endpoint and a real key are configured.
6. Only after validating the probe, proceed with the benchmark workflow below.

Until a real key and authorized data are supplied, real authentication, REST
response behavior, the official streaming handshake, credit consumption, and
transcript quality remain unverified. Mock output must not be used to fit routing
or to claim benchmark performance.

## Benchmark workflow

Run the evaluation scripts from `bharatnoise_api/`. The source manifest for
`prepare_real_set.py` is a labeled CSV with `id,audio_path,language,reference,
speaker_id` and optional `group` columns. Noise input is a directory containing
one subdirectory per noise type. Keep real development and test sets separate.

1. Prepare 8 kHz mono clips and a speaker-disjoint development/test split:

   ```sh
   python scripts/prepare_real_set.py --input-csv data/source.csv \
     --output-dir data/real --seed 41
   ```

2. Generate telephone-channel mixtures for each split. The scripts record the
   target and measured mixture SNRs and write split-specific `snr_dev.csv` and
   `snr_test.csv` files:

   ```sh
   python scripts/build_eval_set.py --manifest data/real/dev.csv \
     --noise-dir data/noise --output-dir data/eval_dev --snrs 0 5 10 --seed 41
   python scripts/build_eval_set.py --manifest data/real/test.csv \
     --noise-dir data/noise --output-dir data/eval_test --snrs 0 5 10 --seed 41
   ```

3. Build candidate manifests with identical clip IDs. Request C3 only if
   DeepFilterNet is installed and reported available:

   ```sh
   python scripts/make_candidate_manifests.py --manifest data/eval_dev/dev.csv \
     --output-dir data/candidates_dev --candidates C0 C1 C2 --seed 41
   python scripts/make_candidate_manifests.py --manifest data/eval_test/test.csv \
     --output-dir data/candidates_test --candidates C0 C1 C2 --seed 41
   ```

4. Run serial baselines for each candidate and model. The same C0 development
   audio is also run again with a distinct output and `--run-id` to measure
   run-to-run variation:

   ```sh
   python run_baseline.py --input data/candidates_dev/C0.csv --output results/dev_C0_v2_5.csv --candidate C0 --model v2.5 --run-id dev-C0-v2.5
   python run_baseline.py --input data/candidates_dev/C0.csv --output results/dev_C0_v3_0.csv --candidate C0 --model v3.0 --run-id dev-C0-v3.0
   python run_baseline.py --input data/candidates_dev/C0.csv --output results/dev_C0_v2_5_repeat.csv --candidate C0 --model v2.5 --run-id dev-C0-v2.5-repeat
   python run_baseline.py --input data/candidates_dev/C1.csv --output results/dev_C1_v2_5.csv --candidate C1 --model v2.5 --run-id dev-C1-v2.5
   python run_baseline.py --input data/candidates_dev/C1.csv --output results/dev_C1_v3_0.csv --candidate C1 --model v3.0 --run-id dev-C1-v3.0
   python run_baseline.py --input data/candidates_dev/C2.csv --output results/dev_C2_v2_5.csv --candidate C2 --model v2.5 --run-id dev-C2-v2.5
   python run_baseline.py --input data/candidates_dev/C2.csv --output results/dev_C2_v3_0.csv --candidate C2 --model v3.0 --run-id dev-C2-v3.0
   ```

   Each command is serialized by the benchmark client. Before making calls, add
   `--dry-run` to validate a manifest and report planned calls/minutes without
   contacting an endpoint. Run test manifests only after router fitting, writing
   their CSVs to separate files under `results/`.

5. Fit the router from development results only. The fitter requires at least ten
   eligible development clips and compares improvements to the measured repeat
   run difference:

   ```sh
   python scripts/fit_router.py \
     --candidate C0=results/dev_C0_v2_5.csv \
     --candidate C1=results/dev_C1_v2_5.csv \
     --candidate C2=results/dev_C2_v2_5.csv \
     --snr-csv data/eval_dev/snr_dev.csv \
     --repeat-a results/dev_C0_v2_5.csv \
     --repeat-b results/dev_C0_v2_5_repeat.csv \
     --output config/router.json --seed 41
   ```

   If a candidate does not improve on C0 beyond the observed repeat-run noise,
   the fitted rules route all SNR ranges to C0.
6. Run every candidate/model on `data/candidates_test/` after fitting; do not use
   test results to tune the router. For C0–C2, the commands are:

   ```sh
   python run_baseline.py --input data/candidates_test/C0.csv --output results/test_C0_v2_5.csv --candidate C0 --model v2.5 --run-id test-C0-v2.5
   python run_baseline.py --input data/candidates_test/C0.csv --output results/test_C0_v3_0.csv --candidate C0 --model v3.0 --run-id test-C0-v3.0
   python run_baseline.py --input data/candidates_test/C1.csv --output results/test_C1_v2_5.csv --candidate C1 --model v2.5 --run-id test-C1-v2.5
   python run_baseline.py --input data/candidates_test/C1.csv --output results/test_C1_v3_0.csv --candidate C1 --model v3.0 --run-id test-C1-v3.0
   python run_baseline.py --input data/candidates_test/C2.csv --output results/test_C2_v2_5.csv --candidate C2 --model v2.5 --run-id test-C2-v2.5
   python run_baseline.py --input data/candidates_test/C2.csv --output results/test_C2_v3_0.csv --candidate C2 --model v3.0 --run-id test-C2-v3.0
   ```

   Repeat the same pattern for C3 only when DeepFilterNet is available. Keep all
   test-run CSVs separate from development and repeat-run CSVs.
7. Aggregate the official measured CSVs:

   ```sh
   python scripts/aggregate_results.py --results-dir results --n-boot 2000 --seed 41
   ```

   The aggregator rejects mock transcripts and reports an actionable error when
   measured input CSVs are absent. No metric is inserted by hand.

## Configuration and references

See [README_API.md](bharatnoise_api/README_API.md) for all backend routes and
environment variables, and [proxy-contract.md](docs/proxy-contract.md) for the
frontend wire contract. Optional Silero VAD and DeepFilterNet integrations are
reported with availability reasons and install hints; the core backend does not
require them.
