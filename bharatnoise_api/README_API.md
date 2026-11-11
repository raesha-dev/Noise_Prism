# BharatNoise API

FastAPI proxy and offline evaluation tools for the BharatNoise workstation. Audio is
decoded to mono float32 at 8 kHz, held in memory for the configured TTL, and sent to
Prisma only through a single queued worker. The browser never receives the Gnani key.

## Setup

Create and activate a project-local environment (Homebrew-managed system Python
should not be modified), install the core dependencies, and run from the repository
root:

```text
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r bharatnoise_api/requirements-api.txt
uvicorn server:app --workers 1
```

Python 3.10 through 3.14 are supported. Optional integrations are listed in
`requirements-optional.txt`; the core installation does not require them.

The root `server.py` is an entry-point shim. The API files and configuration live in
`bharatnoise_api/`. The same server can be run from that directory with
`uvicorn server:app --workers 1`.

## Environment

| Variable | Purpose | Default |
| --- | --- | --- |
| `GNANI_API_KEY` | Server-side Prisma credential; required for transcription and live streaming. Never sent to a browser. | unset |
| `BN_CORS_ORIGINS` | Comma-separated allowed browser origins. | `http://localhost:5173,http://localhost:3000` |
| `BN_DEMO_TOKEN` | When set, POST routes require `X-Demo-Token`; `/ws/live` requires `?token=`. | unset |
| `BN_MAX_UPLOAD_MB` | Maximum request audio size. | `5` |
| `BN_TTL_MINUTES` | Uploaded clip and generated audio lifetime. | `30` |
| `BN_DAILY_CALL_CAP` | Worst-case Prisma calls reserved when a job is queued. | `300` |
| `GNANI_WS_URL` | Upstream streaming URL (primarily for local tests). | `wss://api.vachana.ai/stt/v3/stream` |
| `BN_LIVE_MAX_SECONDS` | Maximum duration of a live connection. | `300` |
| `BN_LIVE_STOP_GRACE_S` | Time to wait after forwarding stop/commit. | `3` |

`GET /api/config` reports optional dependency availability and every unfitted flag.
The shipped router and suppression parameter files are placeholders with `fitted: false`.

## API routes

JSON errors use `{"error":{"code":"...","message":"..."}}`.

| Route | Request / response |
| --- | --- |
| `GET /api/health` | Health and in-memory clip/job counts; no secrets. |
| `GET /api/config` | Languages, models, modes, candidate availability/reasons, limits, fitted flags, live settings and daily budget. |
| `GET /api/samples` | Sample metadata from `samples/index.json`, excluding audio paths. Empty until sample metadata is installed. |
| `POST /api/clips` | Multipart `file` **or** `sample_id`, optional `language` and `reference`; returns `clip_id`, real analysis, variants and TTL. |
| `GET /api/clips/{id}` | Clip metadata and analysis. |
| `DELETE /api/clips/{id}` | Deletes the clip; returns 204. |
| `GET /api/clips/{id}/audio?variant=C0` | Candidate audio as 8 kHz PCM WAV. |
| `GET /api/clips/{id}/spectrogram.png?variant=C0` | Measured 0–4 kHz spectrogram PNG. |
| `POST /api/jobs` | JSON `{"clip_id":"...","mode":"raw|fast|compare_all|accuracy","language":"hi-IN","model":"v2.5","reference":"..."}`; returns 202 and `job_id`. |
| `GET /api/jobs/{id}` | Polls `queued`, `running`, `done` or `error`, including measured hypotheses and scores. |
| `POST /api/score` | JSON `{"reference":"...","hypothesis":"..."}`; returns benchmark-normalized WER/CER, S/D/I and word alignment. |
| `GET /api/results` | Tags and row counts for existing results CSVs. |
| `GET /api/results/compare?a=C0&b=C1&by=language&n_boot=2000` | Paired CSV comparison; deltas are B minus A. `by` may be `group` or `language`. |
| `GET /api/results/{tag}/rows?group=traffic_snr0&limit=200` | Filtered benchmark CSV rows. Tags are restricted to safe filename characters. |
| `GET /api/results/tables` | Aggregate CSV rows; returns 404 until all aggregate files have been produced. |
| `WS /ws/live?language=hi-IN&model=v2.5&variant=C1` | Gnani streaming proxy. Browser sends PCM16 bytes and optional `{"type":"commit"}` / `{"type":"stop"}` frames. |

### Clip upload

```text
curl -X POST http://localhost:8000/api/clips \
  -F file=@speech.wav -F language=hi-IN -F reference="spoken words"
```

To select a bundled clip, send `-F sample_id=<id>` instead of `file`.

### Queue and poll a job

```text
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{"clip_id":"<clip-id>","mode":"fast","language":"hi-IN","model":"v2.5"}'
curl http://localhost:8000/api/jobs/<job-id>
```

Prisma calls are serialized. `raw` uses C0; `fast` uses the SNR router and guard;
`compare_all` calls each available candidate on the requested model; `accuracy` calls
each available candidate on both models and applies minimum-Bayes-risk consensus.
`indic-auto` is accepted only with v3.0 and is rejected for accuracy mode.

### Frontend-compatible routes

The browser adapter in `src/lib/bharatnoise-api.ts` uses these compatibility routes:

* `GET /health` → `{"status":"ok","gnani_configured":false}` when no key is present.
* `POST /process` accepts the UI multipart fields (`file`, `mode`, `sample_rate`,
  `channels`, `sample_format`, `asr_provider`, `asr_model`, `candidates`, `selection`,
  and `compare_models`). It returns snake_case `selected_candidate`, `transcription`,
  `candidates` with playable `audio_url` and measured peaks, analysis, pipeline stages,
  and timing. WER/CER are included only when a reference was supplied.
* `GET /evaluation` exposes generated aggregate CSVs in the UI's evaluation shape; it
  returns 404 rather than sample metrics before aggregate results exist.
* `WS /live` accepts the browser's initial `config` JSON frame and relays upstream
  messages unchanged. It adds the required Gnani upgrade headers itself.

The `/ws/live` WebSocket contract is documented in its route docstring and follows
the upstream streaming schema: 8 kHz mono PCM16 LE, exact 1,024-byte / 64 ms frames,
real-time pacing, silence when input is late, one-second trailing silence, then commit.

## Offline tests

```text
cd bharatnoise_api
pytest -q
```

Tests use synthetic audio, a fake REST client, and a localhost WebSocket upstream.

## Benchmark command order

Run all commands from `bharatnoise_api/`. Use only approved, labeled audio; no test
manifest is used for router fitting.

1. Prepare and speaker-split the labeled set:

   ```text
   python scripts/prepare_real_set.py --input-csv data/source.csv --output-dir data/real --seed 41
   ```

2. Build noisy telephone-channel development and test sets:

   ```text
   python scripts/build_eval_set.py --manifest data/real/dev.csv --noise-dir data/noise --output-dir data/eval_dev --snrs 0 5 10 --seed 41
   python scripts/build_eval_set.py --manifest data/real/test.csv --noise-dir data/noise --output-dir data/eval_test --snrs 0 5 10 --seed 41
   ```

3. Generate matching candidate manifests:

   ```text
   python scripts/make_candidate_manifests.py --manifest data/eval_dev/dev.csv --output-dir data/candidates_dev --candidates C0 C1 C2 --seed 41
   python scripts/make_candidate_manifests.py --manifest data/eval_test/test.csv --output-dir data/candidates_test --candidates C0 C1 C2 --seed 41
   ```

4. Run serial baselines for every candidate and each model. Repeat the same C0 dev
   manifest with a different `--run-id` to measure Prisma run-to-run noise:

   ```text
   python run_baseline.py --input data/candidates_dev/C0.csv --output results/dev_C0_v2_5.csv --candidate C0 --model v2.5 --run-id dev-C0-v2.5-a --seed 41
   python run_baseline.py --input data/candidates_dev/C0.csv --output results/repeats/dev_C0_v2_5_repeat.csv --candidate C0 --model v2.5 --run-id dev-C0-v2.5-b --seed 41
   python run_baseline.py --input data/candidates_dev/C0.csv --output results/dev_C0_v3_0.csv --candidate C0 --model v3.0 --run-id dev-C0-v3.0 --seed 41
   ```

   Repeat the per-candidate/per-model command for each generated C1, C2 and (when
   available) C3 manifest, then repeat C0 for v3.0 if fitting against v3.0. Use a
   distinct output and `--run-id` for every run. Do the same for each test candidate
   and model after fitting, using `data/candidates_test/`.

5. Fit only on dev results, using two same-audio baseline runs and dev SNR estimates:

   ```text
   python scripts/fit_router.py --candidate C0=results/dev_C0_v2_5.csv --candidate C1=results/dev_C1_v2_5.csv --candidate C2=results/dev_C2_v2_5.csv --snr-csv data/eval_dev/snr.csv --repeat-a results/dev_C0_v2_5.csv --repeat-b results/repeats/dev_C0_v2_5_repeat.csv --output config/router.json --seed 41
   ```

6. Run every candidate and model on the held-out test manifests only after fitting.
   Keep those CSVs separate from dev rows.

7. Aggregate the resulting measured CSV files:

   ```text
   python scripts/aggregate_results.py --results-dir results --n-boot 2000 --seed 41
   ```

Aggregate values are calculated from the CSV rows. No benchmark values are embedded
in the API or generated documentation.
