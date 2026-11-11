# BharatNoise API

FastAPI proxy and evaluation tooling for the React/TypeScript workstation. Audio
is decoded to mono float32 at 8 kHz, held in memory until its TTL expires, and
transcribed by a single serial job worker. Gnani credentials remain server-side.

## Setup and run

Use the project-local environment so Homebrew-managed Python is not modified:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r bharatnoise_api/requirements-api.txt
python -m uvicorn server:app --workers 1
```

The backend supports Python 3.10 through 3.14. From `bharatnoise_api/`, the
equivalent server command is `python -m uvicorn server:app --workers 1`.
`bharatnoise_api.server:app` also works from the repository root. Directories
for clips, cache, and results are created only when needed and are rooted in
`bharatnoise_api/` by default; set `BN_DATA_DIR` to use another data root.

The core install does not need Silero VAD, DeepFilterNet, or python-dotenv.
They are listed in `requirements-optional.txt`. Missing optional integrations
are reported in `/api/config` with a reason and install hint.

## Configuration

The backend loads `bharatnoise_api/.env` and then the repository-root `.env`;
real environment variables take precedence. Copy the root `.env.example` to
`.env` for local configuration. Never put secrets in frontend variables.

| Variable | Purpose | Default |
| --- | --- | --- |
| `GNANI_API_KEY` | Server-side API key; never logged or returned. | Unset |
| `GNANI_BASE_URL` | Gnani REST origin. | `https://api.vachana.ai` |
| `GNANI_WS_URL` | Gnani streaming endpoint. | `wss://api.vachana.ai/stt/v3/stream` |
| `BN_DATA_DIR` | Root for runtime work, cache, and official results directories. | `bharatnoise_api/` |
| `BN_CORS_ORIGINS` | Comma-separated browser origins. | `http://localhost:5173,http://localhost:3000,http://localhost:8080,http://127.0.0.1:8080` |
| `BN_DEMO_TOKEN` | When set, POST routes require `X-Demo-Token`; live WebSockets require `?token=`. | Unset |
| `BN_MAX_UPLOAD_MB` | Maximum audio upload size in MiB. | `5` |
| `BN_TTL_MINUTES` | Uploaded clip and generated audio lifetime. | `30` |
| `BN_DAILY_CALL_CAP` | Worst-case API calls reserved when queuing a job. | `300` |
| `BN_LIVE_MAX_SECONDS` | Maximum live session duration. | `300` |
| `BN_LIVE_STOP_GRACE_S` | Wait after the final live commit. | `3` |
| `VITE_BHARATNOISE_API_URL` | Browser-visible backend origin; do not put credentials here. | `http://127.0.0.1:8000` |

`gnani_mode` is `unconfigured` without a key, `live` when the configured API
host is `api.vachana.ai`, and `mock` when a key is set for any other API host.
For the local mock use `GNANI_API_KEY=mock-key`,
`GNANI_BASE_URL=http://127.0.0.1:9100`, and
`GNANI_WS_URL=ws://127.0.0.1:9100/stt/v3/stream`. Mock transcripts start with
`[MOCK]`, mock model names start with `mock-`, and scores are suppressed.
Mock baseline outputs go to `results_mock/`; that directory is not served by
the results API.

## HTTP routes

HTTP errors have the form `{"error":{"code":"...","message":"..."}}`.

| Method and route | Contract |
| --- | --- |
| `GET /health` | Legacy health response: `status`, `gnani_configured`, `version`, `gnani_mode`. |
| `GET /api/health` | Health and in-memory counts: legacy fields plus `ok`, `gnani_key_configured`, `clips_in_memory`, `jobs_in_memory`. Contains no secret. |
| `GET /api/config` | Languages, models, modes, candidate availability/reasons, limits, fitted flags, optional dependency information, live settings, demo-token state, and remaining call budget. |
| `GET /api/samples` | Sample metadata from `samples/index.json`, with audio file paths omitted. |
| `POST /api/clips` | Multipart `file` **or** `sample_id`, optional `language` and `reference`; returns HTTP 201 with clip ID, analysis, variants, and TTL. |
| `GET /api/clips/{clip_id}` | Clip metadata, analysis, variants, and candidate parameters. |
| `DELETE /api/clips/{clip_id}` | Delete an in-memory clip; HTTP 204. |
| `GET /api/clips/{clip_id}/audio?variant=C0` | Candidate audio as an 8 kHz WAV. |
| `GET /api/clips/{clip_id}/spectrogram.png?variant=C0` | Spectrogram PNG covering 0–4 kHz. |
| `POST /api/jobs` | JSON job request; HTTP 202 and `job_id`. |
| `GET /api/jobs/{job_id}` | `queued`, `running`, `done`, or `error`, with progress steps and result/error. |
| `POST /api/score` | JSON `{"reference":"...","hypothesis":"..."}`; benchmark-normalized WER/CER, S/D/I, and word-level operations. |
| `GET /api/results` | Tags and row counts for official CSV files under `results/`. |
| `GET /api/results/compare?a=C0&b=C1&by=language&n_boot=2000` | Paired comparison with delta B minus A, bootstrap interval, and win/tie/loss; `by` may be `group` or `language`. |
| `GET /api/results/{tag}/rows?group=traffic_snr0&limit=200` | Filter CSV rows; tags are restricted to safe filename characters. |
| `GET /api/results/tables` | Aggregate CSV rows; returns an explicit 404 error until all required aggregate files exist. |
| `GET /evaluation` | Legacy evaluation response. Before aggregates exist, returns `available:false`, a reason, null metadata, and empty sections. |
| `POST /process` | Legacy workstation upload contract; see [proxy-contract.md](../docs/proxy-contract.md). |

`POST /api/jobs` accepts:

```json
{
  "clip_id": "clip-id",
  "mode": "fast",
  "language": "hi-IN",
  "model": "v2.5",
  "reference": "optional reference"
}
```

`model` is `v2.5` or `v3.0`. Modes are `raw`, `fast`, `compare_all`, and
`accuracy`. `raw` sends C0; `fast` analyzes, routes, guards, then makes one call;
`compare_all` calls all available candidates on the requested model; `accuracy`
calls all available candidates on both models and chooses minimum-edit-distance
consensus. `indic-auto` is allowed only with v3.0, and not in `accuracy` mode.
Prisma calls use one in-process queue and are never dispatched concurrently.

## Live WebSockets

Both `WS /ws/live` and its legacy alias `WS /live` are documented in the server
route docstring. Query parameters include `language`, `model`, `variant` (`C0`
or `C1`), `sample_rate=8000`, `vad_preset=telephony`, VAD settings, and optional
demo `token`. The browser may send PCM16 mono binary chunks of any size and JSON
`{"type":"stop"}` or `{"type":"commit"}` text frames. The proxy buffers and
reframes audio to exact 1,024-byte packets on the 64 ms cadence, adds Gnani's
upgrade headers, sends trailing silence and commit on stop, and relays upstream
JSON frames unchanged. The configured live session cap is enforced.

## Offline validation

```sh
make test
make e2e
```

Tests use synthetic audio, a fake REST client, and localhost WebSocket services.
They do not require a Gnani key or call the official API. `make preflight` is the
separate check for configured endpoints; the script prints
`NO KEY: nothing was verified` and exits with code 3 when no key is configured.
GNU Make reports a failing recipe as make exit code 2.

## Benchmark workflow

The exact command sequence, including development-only router fitting and
held-out testing, is documented in the repository [README](../README.md).
The key boundaries are: validate planned calls first, keep all API work serial,
fit only on development rows, never use mock transcripts for fitting or
aggregation, and write official outputs only under `results/`.

`config/router.json` and `config/suppress.json` ship with `fitted: false`.
Placeholder thresholds and suppression parameters are not measured results.
