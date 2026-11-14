# BharatNoise workstation API contract

The browser sends audio to the BharatNoise backend, not to Gnani. The server
keeps `GNANI_API_KEY` private. Set `VITE_BHARATNOISE_API_URL` to the backend
origin; the default is `http://127.0.0.1:8000`. The client validates successful
JSON responses with Zod before using them. Contract failures are surfaced as
errors rather than rendered as partial results.

HTTP errors use:

```json
{ "error": { "code": "...", "message": "..." } }
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

| Field                                      | Contract                                                                                                                                                                                         |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `file`                                     | Audio clip; backend decodes/resamples to mono 8 kHz.                                                                                                                                             |
| `mode`                                     | `fast` or `max`.                                                                                                                                                                                 |
| `sample_rate`, `channels`, `sample_format` | Must be `8000`, `1`, and `pcm_s16le`.                                                                                                                                                            |
| `asr_provider`                             | Must be `gnani_prisma`.                                                                                                                                                                          |
| `asr_model`                                | `prisma-v2.5` or `prisma-v3.0`.                                                                                                                                                                  |
| `language_code`                            | Supported Indian language code, or `indic-auto` with Prisma v3.0.                                                                                                                                |
| `format`                                   | Optional: `verbatim`, `transcribe`, `codemix`, or `transliterate`; `codemix` and `transliterate` require Prisma v3.0. Omit it (the UI's Automatic option) to use Gnani's model/language default. |
| `candidates`                               | Comma-separated `C0`–`C3`, `C2B`, and/or `C2C`; unavailable optional variants are not run.                                                                                                      |
| `oa_lambda`                                | Optional Observation Addition coefficient for C2C, in `[0, 0.4]`; default `0.1`.                                                                                                                |
| `selection`                                | `snr_router` for Fast; `consensus` for Max.                                                                                                                                                      |
| `compare_models`                           | Max only. `true` transcribes on both models; otherwise only `asr_model`.                                                                                                                         |
| `reference`                                | Optional transcript reference used for score computation.                                                                                                                                        |

Legacy modes map to `/api/jobs` as follows:

| Legacy fields                      | API job behavior                                                                                                                                                   |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `mode=fast`                        | `fast`: analyze, route, guard, and transcribe the chosen candidate once on `asr_model`.                                                                            |
| `mode=max`, `compare_models=false` | `compare_all`: transcribe available requested candidates on `asr_model`; select minimum-edit-distance consensus.                                                   |
| `mode=max`, `compare_models=true`  | `accuracy`: transcribe available candidates on both models, requested model first; the legacy output presents the requested-model candidate results and selection. |

The success response is strictly validated against the TypeScript adapter and
Pydantic response model. It includes `request_id`, `gnani_mode`,
`output_format`, `language_code`, `detected_language_code`,
`unavailable_candidates`, `selected_candidate`, `transcription`, `candidates`,
`analysis`, `evaluation`, `pipeline`, `latency_ms`, `router_fitted`,
`suppression_fitted`, and `score_suppressed_reason`. Candidate fields are
`transcript`, `audio_url`, `peaks`, `guard_score`, `confidence`, `snr_db`,
`error`, `truncated`, and processing `parameters`. C2B is simplified two-pass
spectral noise tracking with log-spectral gain; cited-paper fidelity is
unverified. C2C adds the selected lambda times the original signal to C2B.
Empty `transcript` with no `error` means the request succeeded but Gnani
returned no text; a non-empty `error` identifies a failed candidate request.
`truncated` reflects Gnani's response flag. Gnani does not return confidence
scores. C2B applies the experimental spectral filter, attenuates VAD-labeled
non-speech, and applies capped speech leveling; C2C adds its configured share
of the original and may restore noise. Their parameters remain unfitted, and
audio changes do not establish WER improvement. Fast mode transcribes only
its routed candidate; Max mode requests transcripts for the selected candidate
set. Analysis reports
`stationary_snr_db_est` (always present; `null` when unavailable),
`noise_floor_db`, `speech_energy_db`, `vad_segments`, guard fields,
and `spectrum`. Evaluation fields are nullable `wer`, `cer`, `latency_ms`,
`calls`, and `note`. WER/CER are absent/null without a reference and are
suppressed in mock mode.

The response also includes the effective `output_format`, requested
`language_code`, Prisma v3.0's `detected_language_code` when returned, and
`unavailable_candidates` with reasons for requested optional candidates that
the backend could not run. Language detection and output style are separate
controls. `indic-auto` asks Prisma v3.0 to identify the spoken language; it
does not choose among the four formats based on the heard script. Per Gnani's
[REST format documentation](https://docs.gnani.ai/api/STT/speech-to-text.md)
and [Prisma v3.0 output-style documentation](https://docs.gnani.ai/api/STT/models/prisma-v3.md#output-styles),
omitting `format` uses the model/language default (for auto language detection,
that default is `verbatim`).

Max-mode consensus ignores failed and empty transcripts. If no candidate has
non-empty text but at least one request succeeded, the response still includes
candidate diagnostics and an empty final transcript rather than treating an
empty result as a consensus vote.

## `POST /api/tts`

The browser sends the entire displayed transcript as JSON:

```json
{ "text": "..." }
```

The backend sends it to Gnani Timbre with automatic language selection, then
returns the generated WAV as `audio/wav` for the browser audio player. The
Gnani credential stays on the backend, transcript text is not logged, and this
request consumes one call from the configured daily Gnani request budget.
The endpoint accepts at most 10,000 characters and uses the same optional
`X-Demo-Token` protection as other protected API requests. Failures use the
standard JSON error shape; the browser never treats an error response as audio.

The backend configuration endpoint lists C2B and C2C as experimental and
returns simplified spectral defaults and the supported OA sweep coefficients.
The legacy `imcra_omlsa` configuration property remains for API compatibility;
it does not assert paper-faithful algorithm implementations. The default
C2-candidate request remains unchanged; callers must explicitly include
C2B/C2C to spend additional Prisma calls.

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
