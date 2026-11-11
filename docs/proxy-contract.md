# BharatNoise workstation API contract

The browser sends audio only to the BharatNoise backend. Gnani credentials stay
server-side. Set `VITE_BHARATNOISE_API_URL` to the backend origin; local development
uses the configured default. Requests and responses use JSON except for multipart
audio uploads, WAV/PNG media, and the live WebSocket.

All HTTP errors use `{"error":{"code":"...","message":"..."}}`. The TypeScript
adapter validates successful JSON responses and reports a contract error instead
of interpreting an unknown shape.

## `GET /health`

Returns `status`, `gnani_configured`, and `version`. The browser measures this
request's elapsed time locally; it is not Prisma latency. `GET /api/health` is a
separate extended API response with process counts.

## `POST /process`

`multipart/form-data` fields:

| Field | Contract |
|---|---|
| `file` | Audio clip; backend decodes to mono 8 kHz. |
| `mode` | `fast` or `max`. |
| `sample_rate`, `channels`, `sample_format` | Must be `8000`, `1`, and `pcm_s16le`. |
| `asr_provider` | Must be `gnani_prisma`. |
| `asr_model` | `prisma-v2.5` or `prisma-v3.0`. |
| `candidates` | Comma-separated candidate IDs. Unavailable optional candidates are omitted. |
| `selection` | `snr_router` for Fast; `consensus` for Max. |
| `compare_models` | Max only. `true` transcribes on both models; otherwise only `asr_model` is used. |
| `reference` | Optional human-verified reference for WER/CER computation. |

Legacy mode mapping to `/api/jobs`:

| Legacy fields | API job behavior |
|---|---|
| `mode=fast` | Queues `fast`: route, guard, and transcribe the chosen candidate once on `asr_model`. |
| `mode=max`, `compare_models=false` | Queues `compare_all` on `asr_model`, then selects the minimum-edit-distance consensus among that model's successful candidate transcripts. |
| `mode=max`, `compare_models=true` | Queues `accuracy` on both models, with `asr_model` first. The legacy response contains the requested model's successful candidate transcripts and selection. |

The successful response includes `request_id`, `selected_candidate`, `transcription`,
`candidates`, `analysis`, `evaluation`, `pipeline`, `latency_ms`, `router_fitted`,
and `suppression_fitted`. Each candidate has `transcript`, `audio_url`, `peaks`,
`guard_score`, `confidence`, and `snr_db`; fields not measured by this backend are
`null`. There are no Gnani confidence scores. Analysis contains `snr_db`,
`noise_floor_db`, `speech_energy_db`, `vad_segments`, `guard_status`,
`guard_result`, and `spectrum`. Evaluation contains `wer`, `cer`, `latency_ms`,
`calls`, and `note`. WER/CER are `null` unless a reference was supplied and the
backend mode is eligible to report a measured score.

## `GET /evaluation`

Returns one of these states:

- Results available: `available: true`, nullable dataset metadata, and the
  `comparison`, `ablation`, `snr`, and `noise` measured row arrays.
- No results: `available: false`, a reason, null metadata, and empty arrays.

The empty response does not contain placeholder rows or metric values. The UI
renders an explicit no-results state.

## WebSocket `/live`

The workstation connects to `/live?sample_rate=8000&vad_preset=telephony`, then
sends a JSON config frame followed by binary audio. Audio is mono PCM16 little
endian, exactly 1,024 bytes (512 samples) per frame, paced at 64 ms. The backend
adds Gnani's required upgrade headers and relays upstream JSON messages. Browser
code validates `connected`, `speech_start`, `processing`, `transcript`, and
`error` messages; a malformed or unknown message is surfaced as a live error.
Browsers cannot set the upstream Gnani headers.

The live route's FastAPI docstring documents its WebSocket contract; WebSocket
operations are not part of FastAPI's HTTP OpenAPI path list.
