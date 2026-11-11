# BharatNoise UI/backend contract audit

This is the post-implementation audit of the frontend adapter and workstation
surfaces against the current FastAPI implementation. Successful legacy HTTP
responses are validated by Zod in the adapter and documented by Pydantic response
models. The backend handlers are in `bharatnoise_api/server.py`.

## UI calls and wire contract

| UI call | Method and route | Request fields | Response fields the UI reads | Backend handler | Status |
| --- | --- | --- | --- | --- | --- |
| Startup/connection check | `GET /health` | None | `status`, `gnani_configured`, `gnani_mode`, `version` | `health()` | OK |
| Pipeline processing | `POST /process` multipart | `file`, `mode`, `sample_rate`, `channels`, `sample_format`, `asr_provider`, `asr_model`, `candidates`, `selection`, `compare_models` | `request_id`, `gnani_mode`, selected candidate/transcription, candidate transcript/audio/peaks/nullable scores, analysis, evaluation, pipeline, latency, fitted flags, score-suppression reason | `frontend_process()` | OK |
| Evaluation results | `GET /evaluation` | None | `available`, `reason`, metadata, and comparison/ablation/SNR/noise rows | `frontend_evaluation()` | OK |
| Live transcription | WebSocket `/live` | Query: language/model/variant, 8 kHz sample rate, telephony VAD settings, optional token; browser sends config JSON and binary chunks; stop/commit JSON | Gnani `connected`, `speech_start`, `processing`, `transcript`, and `error` frames; adapter validates supported shapes | `live()` and `relay()` | OK |
| Candidate audio playback | `GET` of candidate `audio_url` from `/process` | Candidate URL | 8 kHz WAV decoded by the browser | `clip_audio()` via the clip audio route | OK |
| Reference scoring in Transcript Studio | Local calculation; no separate HTTP call | API transcript, user-entered reference | Browser-computed WER/CER and token differences; hidden in mock mode | `wordErrorRate`, `characterErrorRate`, `diffWords` in `src/lib/metrics.ts` | OK — derived from current service text and explicit user reference; not benchmark CSV data |
| Evaluation JSON import | Local file import; no HTTP call | User-selected JSON | Validated evaluation fields and rows | `parseEvaluationReport()` in `src/lib/bharatnoise-api.ts` | OK — malformed shapes raise a contract error |
| Candidate waveform | Audio samples from uploaded/service WAV | Selected candidate | Browser-rendered waveform peaks | `AudioStudio` and shared waveform renderer | OK — waveform is derived from clip audio or service audio/peaks, not illustrative placeholder data |

Legacy mode mapping: `fast` maps to API job mode `fast`; `max` with
`compare_models=false` maps to `compare_all`; `max` with
`compare_models=true` maps to `accuracy`. See
[proxy-contract.md](proxy-contract.md) for request and response details.

## Number and transcript display inventory

| UI location | Displayed values | Data source |
| --- | --- | --- |
| Header and Settings | Service status, version, Gnani mode, backend reachability duration | `/health` response; duration is measured locally around that request and is not reported as Prisma latency |
| Audio Studio | Clip duration, sample-rate/channel labels, playback time, waveform | Uploaded/recorded file metadata and decoded samples; candidate audio and peaks from `/process`; no-clip values are empty |
| Analysis Panel | Estimated SNR, noise floor, speech energy, VAD segments, spectrum, guard state | Analysis fields in the validated `/process` response; the panel shows an empty state before backend analysis |
| Pipeline Panel | Candidate, route note, stages, API calls and elapsed time | `/process` response and server pipeline metadata; stage animation is UI progress, not an ASR measurement |
| Transcript Studio | Candidate transcript text and differences | `transcription`/`candidates` in `/process`; mock transcript strings retain their `[MOCK]` prefix and the app shows its mock banner |
| Transcript Studio score | WER/CER against a reference | Computed in the browser from the current service transcript and user-entered reference; suppressed in mock mode; distinct from aggregate benchmark results |
| Evaluation Panel | WER, CER, latency, deltas, charts, clip count, measurement date | `/evaluation` rows backed by aggregate result CSVs, or a user-imported validated JSON; displays an explicit no-results state for an unavailable report and hides scores in mock mode |
| Evaluation service-run summary | WER/CER, calls, latency | Last `/process` response; mock WER/CER are suppressed |
| Live Panel | Input level, sent-frame count, local elapsed time, upstream latency/SNR, transcript | Local microphone/capture measurements for input level and frame stats; latency/SNR/transcript from relayed live messages; mock transcript prefix is retained |
| History Panel | Run time, selected candidate, SNR, latency, file name | Browser-local history populated from prior upload/process responses; mock score fields are not persisted as benchmark metrics |
| JSON/CSV exports | Process output or displayed evaluation rows | Last validated `/process` response or active evaluation report; no locally invented benchmark rows |

The frontend does not contain benchmark result tables or a transcript fallback.
`GET /evaluation` returns an empty response shape until aggregate CSVs exist.
Mock scores remain suppressed throughout processing, history, and evaluation.
