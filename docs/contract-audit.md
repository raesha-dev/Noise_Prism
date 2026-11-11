# BharatNoise UI/backend contract audit

This audit records the contracts observed in the frontend adapter and workstation
components against the current FastAPI handlers. “Response fields” lists fields
that the browser reads after parsing; the live socket row lists message variants.
The evaluation route's current 404 is an intentional mismatch with the required
explicit empty state and is not treated as a successful response.

## Calls and wire contract

| UI call | Method and route | Request fields | Response fields the UI reads | Backend handler | Status |
|---|---|---|---|---|---|
| Connection test and startup check | `GET /health` | None | `status`, `gnani_configured`, `version`; browser measures request latency | `health()` in `bharatnoise_api/server.py` | MISMATCH — fields are present, but `/health` is not documented with a typed response model and no explicit `gnani_mode` exists. |
| Audio processing | `POST /process` multipart | `file`, `mode`, `sample_rate`, `channels`, `sample_format`, `asr_provider`, `asr_model`, `candidates`, `selection`, `compare_models` | `request_id`, `selected_candidate`, `transcription`, `candidates[id].transcript/audio_url/peaks/guard_score/confidence/snr_db`, `analysis.snr_db/noise_floor_db/speech_energy_db/vad_segments/guard_status/guard_score/spectrum`, `evaluation.wer/cer/latency_ms/calls/note`, `pipeline`, `latency_ms` | `frontend_process()` in `bharatnoise_api/server.py` | MISMATCH — optionality and field names overlap, but there is no response model, score provenance is not mode-aware, and `max`/`compare_models` behavior is not yet reflected in a strict frontend contract. |
| Evaluation report | `GET /evaluation` | None | `dataset`, `clip_count`, `measured_at`, `sections.{comparison,ablation,snr,noise}[]`; each row `id`, `pipeline`, optional `wer/cer/latency_ms/candidate` | `frontend_evaluation()` in `bharatnoise_api/server.py` | MISMATCH — currently returns 404 before aggregate results exist; UI expects a parsed report and shows an error rather than an explicit empty state. |
| Live transcription | WebSocket `/live` | URL query `sample_rate=8000`, `vad_preset=telephony`; browser sends a JSON config frame, then binary PCM16 frames | Adapter understands `{type: partial|final, text, candidate}`, `{type: metrics, latency_ms, snr_db}`, and `{type:error, message}` | `live()` and `relay()` in `bharatnoise_api/server.py` | MISMATCH — backend forwards Gnani's `connected`, `speech_start`, `processing`, `transcript{text,...}`, and `error` frames without converting them to the UI variants; request config frame is not the documented legacy contract. |
| Candidate audio playback and waveform | `GET` of candidate `audio_url` from `/process` | Candidate URL | Binary WAV decoded in browser; or numeric `peaks[]` | `clip_audio()` in `bharatnoise_api/server.py` | OK — waveform peaks and playable WAV are returned for successful requested candidates; URL construction uses the API route. |
| User-reference scoring | Browser-only; no route | User-entered reference and selected transcript | Browser-computed WER/CER fractions; normalized diff tokens | `wordErrorRate`, `characterErrorRate`, `diffWords` in `src/lib/metrics.ts` | MISMATCH — browser scoring can score mock output and must be hidden in mock mode; normalization must remain clearly distinct from benchmark scoring. |
| Imported evaluation report | Browser file import; no route | User-selected JSON | Same fields as evaluation report | `parseEvaluationReport()` in `src/lib/bharatnoise-api.ts` | MISMATCH — the schema currently accepts optional/partial numeric values and has no explicit empty-state form or mock provenance. |

## Number and transcript display inventory

| UI location | Displayed value | Current source | Audit note |
|---|---|---|---|
| `SettingsDialog` and workstation header | API reachability latency, version, Gnani-configured state | Browser-measured `/health` timing and response | Must distinguish `unconfigured`, `mock`, and `live`; timing is a local request measurement. |
| `AudioStudio` | Clip duration, sample rate, channel count, playback position | Browser audio metadata and decoded clip | Measured for a loaded clip; the no-clip duration in `Workstation` is currently hard-coded and must be removed. |
| `AudioStudio` waveform | Amplitude peaks | Uploaded audio, service WAV/peaks, or `illustrativePeaks()` | No-clip preview is deterministic invented data; remove it rather than present as measured audio. |
| `AnalysisPanel` | SNR, noise floor, speech energy, VAD segments, spectrum, guard score/status | Local browser estimate or `/process` response; preview constants when no clip exists | Preview VAD and spectrum are deterministic invented data; remove. Preserve the “estimate” versus “service” labels. |
| `PipelinePanel` | Candidate selection, call count, route, stage detail | Local demo router or `/process` response plus fixed mode copy | Local demo is not backend processing; disable transcription scoring while unconfigured and identify it as a local preview only if retained. |
| `TranscriptPanel` | Transcript text, WER, CER, word differences | `/process` candidates and user-entered reference; browser metric functions | Mock transcripts must be unmistakably labeled; mock WER/CER must be hidden. |
| `EvaluationPanel` | WER, CER, latency, deltas, chart heights, row/clip counts | `/evaluation` aggregate CSV output or explicitly imported JSON | No rows must render as “no results yet”; imported rows need schema validation and mock data must not be presented as benchmark results. |
| `EvaluationPanel` service-run summary | WER/CER, API calls, latency | Last `/process` response | Must hide WER/CER for mock mode and only display measured response fields. |
| `LivePanel` | Elapsed time, frame count, input level, latency, SNR, transcript | Browser capture/frame scheduler and relayed live messages | Simulated mode currently generates a changing level and elapsed/frame values; remove or clearly avoid representing as upstream measurements. |
| `HistoryPanel` | Run time, selected candidate, SNR, latency, file name | Browser-local history from prior service or demo run | History records are user-device state, not benchmark data; mock records must retain mock provenance and hide scores. |
| `SettingsDialog` | Test-connection latency | Browser-measured `/health` request | Not a Prisma latency. |
| Result JSON export | Transcript and response metrics | Last `/process` response | Mock output must remain visibly marked and carry no mock WER/CER. |

## Follow-up

The mismatches above are work items for WP2 and WP6. This file records the
pre-change audit; the final implementation and tests should be checked against
the same call sites and display surfaces.
