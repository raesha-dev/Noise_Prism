# BharatNoise workstation API contract

The browser sends audio to the BharatNoise backend, not to Gnani. The server
keeps `GNANI_API_KEY` private. Set `VITE_BHARATNOISE_API_URL` to the backend
origin; the default is `http://127.0.0.1:8000`. The client validates successful
JSON responses with Zod before using them. Contract failures are surfaced as
errors rather than rendered as partial results.

HTTP errors use:

```json
{"error":{"code":"...","message":"..."}}
```

## `GET /health`

Returns the strict legacy health object:

```json
{
  "status": "ok",
  "gnani_configured": false,
  "version": "0.3.1",
  "gnani_mode": "unconfigured"
}
```

`gnani_mode` is `unconfigured`, `mock`, or `live`. The browser separately
measures the local health-request elapsed time; that is not Prisma latency.
`GET /api/health` is the expanded API health response with process counts.

## `POST /process`

Send `multipart/form-data`:

| Field | Contract |
| --- | --- |
| `file` | Audio clip; backend decodes/resamples to mono 8 kHz. |
| `mode` | `fast` or `max`. |
| `sample_rate`, `channels`, `sample_format` | Must be `8000`, `1`, and `pcm_s16le`. |
| `asr_provider` | Must be `gnani_prisma`. |
| `asr_model` | `prisma-v2.5` or `prisma-v3.0`. |
| `candidates` | Comma-separated `C0`–`C3`; unavailable optional variants are not run. |
| `selection` | `snr_router` for Fast; `consensus` for Max. |
| `compare_models` | Max only. `true` transcribes on both models; otherwise only `asr_model`. |
| `reference` | Optional transcript reference used for score computation. |

Legacy modes map to `/api/jobs` as follows:

| Legacy fields | API job behavior |
| --- | --- |
| `mode=fast` | `fast`: analyze, route, guard, and transcribe the chosen candidate once on `asr_model`. |
| `mode=max`, `compare_models=false` | `compare_all`: transcribe available requested candidates on `asr_model`; select minimum-edit-distance consensus. |
| `mode=max`, `compare_models=true` | `accuracy`: transcribe available candidates on both models, requested model first; the legacy output presents the requested-model candidate results and selection. |

The success response is strictly validated against the TypeScript adapter and
Pydantic response model. It includes `request_id`, `gnani_mode`,
`selected_candidate`, `transcription`, `candidates`, `analysis`, `evaluation`,
`pipeline`, `latency_ms`, `router_fitted`, `suppression_fitted`, and
`score_suppressed_reason`. Candidate fields are `transcript`, `audio_url`,
`peaks`, `guard_score`, `confidence`, and `snr_db`; values not measured by this
backend are null. Gnani does not return confidence scores. Analysis reports
`snr_db`, `noise_floor_db`, `speech_energy_db`, `vad_segments`, guard fields,
and `spectrum`. Evaluation fields are nullable `wer`, `cer`, `latency_ms`,
`calls`, and `note`. WER/CER are absent/null without a reference and are
suppressed in mock mode.

## `GET /evaluation`

Returns a validated evaluation response with `available`, `reason`, `dataset`,
`clip_count`, `measured_at`, and `sections` (`comparison`, `ablation`, `snr`,
`noise`). With no aggregate results, it returns `available: false`, the reason
`No benchmark results have been generated yet.`, null metadata, and empty
sections. No placeholder metric rows are returned. The UI displays this as an
empty state.

## WebSocket `/live`

`/live` is a FastAPI WebSocket endpoint (documented in the route docstring), not
an HTTP request. The browser connects with query parameters such as
`?language=hi-IN&model=v2.5&variant=C0&sample_rate=8000&vad_preset=telephony`.
It sends an initial JSON config frame, then binary PCM16 little-endian mono
audio. The backend accepts arbitrary browser chunk sizes, reframes them as
exactly 1,024-byte packets on the 64 ms cadence, and adds the upstream Gnani
upgrade headers because browsers cannot set them. It relays Gnani's
`connected`, `speech_start`, `processing`, `transcript`, and `error` JSON
messages unchanged. On `stop`, the proxy sends trailing silence, forwards
commit, waits its configured grace period, then closes. The adapter validates
the known message shapes and surfaces malformed messages as errors.

## Data and result provenance

Mock REST responses carry `X-Mock-Gnani: 1`; mock transcript text starts with
`[MOCK]`, and model names start with `mock-`. Mock job scores are null and
include `score_suppressed_reason: "mock"`. Baseline rows against a non-official
host go to `results_mock/`, which is not served by API result routes. Only CSVs
in the configured official results directory are exposed by `/api/results` and
`/evaluation`.
