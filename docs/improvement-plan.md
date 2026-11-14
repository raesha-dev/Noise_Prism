# BharatNoise repository audit

Audit date: 2026-11-14. This is a source and local-data audit before the work
order implementation. No paid Gnani call or benchmark was run for this audit.

## Existing application and reusable behavior

- The React workstation sends audio to `POST /process`; the FastAPI backend
  adapts that request to in-memory clips and jobs. The `/api/jobs` worker is
  the serial REST call path. `GnaniClient` already owns the request lock,
  retry/backoff, explicit model/language/format fields, and run-scoped cache.
- REST request configuration must remain distinct from the `/ws/live` WebSocket
  proxy. Streaming sends 1,024-byte PCM16 frames through the proxy; its VAD
  controls are not REST controls.
- `pipeline.load_audio` decodes through libsndfile, downmixes, resamples to
  mono 8 kHz, and enforces 0.3–30 seconds. C0 returns the prepared original
  without enhancement. C1 is DC removal, a 300–3,400 Hz bandpass, and
  peak-safe gain targeting -20 dBFS RMS. C2 is an STFT/Wiener-style suppressor.
  C3 is optional DeepFilterNet at 48 kHz, resampled from and back to 8 kHz.
- C2B/C2C are already registered opt-in experimental candidates. Their
  implementation is simplified two-pass spectral noise tracking and
  log-spectral gain; cited-paper fidelity is unverified and parameters are
  marked unfitted.
- The speech-preservation guard measures spectral and speech-energy changes.
  Fast mode previously routed using `snr_db_est` (now named
  `stationary_snr_db_est`); that estimate compares speech-frame
  power to the quietest 20% of frames and is not validated for overlapping
  noise. Router and C2 parameters are placeholders.
- Max mode transcribes requested candidates, drops empty/error hypotheses for
  consensus, and selects the hypothesis with the smallest summed word edit
  distance. It has no agreement score, no wrong-script or short-output filter,
  and no confidence score from Gnani.
- The existing result aggregator produces overall, language, noise, SNR,
  latency, and request-diagnostic CSVs. `compare.py` reports paired WER/CER and
  bootstraps by speaker when present, otherwise by utterance. It does not
  cluster by `source_clip_id`, report `n_source_clips`, enforce an
  underpowered-stratum policy, or separate `source_type`.
- Existing scripts include synthetic demo generation, a mixture builder,
  candidate-manifest generation, baseline calling, budget planning, aggregate
  reporting, and dev-only router fitting. The mixture builder is not yet a
  balanced per-clip design, does not enforce the DEMAND license manifest, and
  does not prevent dev/test noise-segment leakage.
- The transcript UI keeps per-candidate transcript/error/empty/truncation
  states and allows manual reference scoring. It warns about detected-language
  mismatch but has no one-click retry. The output-format UI still describes
  `transcribe` as “numbers as digits,” which is misleading. Evaluation supports
  JSON import, CSV download, and service load; a server-side export endpoint,
  source-type/cluster-CI tables, and an UNDERPOWERED marker are absent.
- Tests use synthetic audio and fake clients. The backend suite previously
  passed 81 tests after the VAD guard fix; the current full suite still needs
  rerunning after the final-proof-phase changes.

## C2 observation blend

`audio/suppress.py:suppress` already computes
`enhanced + lambda * original`, then applies a shared peak limiter to 0.99.
This is additive Observation Addition, not a convex interpolation. Lambda is
selected from an SNR table in `config/suppress.json`; these values and the
suppression settings are explicitly `fitted: false`. Any requested OA sweep
must isolate this existing C2 enhancer and change only lambda; the separate
C2B/C2C experiment is not a substitute.

## Local data audit

- The local GramVaani archive exists under ignored `bharatnoise_api/data/`.
  Its inspected layout has Dev/Eval `Audio/*.mp3`, `text`, `utt2labels`,
  `uttids`, and `mp3.scp`, plus a `Metadata/` folder. `text` is the
  utterance-ID-plus-transcript source; `utt2labels` is demographic/annotation
  metadata, not transcript text. No speaker-ID field was found. The local
  importer and format/licence limitations are documented in
  `docs/data-notes.md`.
- The existing GramVaani import produced 1,132 Dev and 560 Eval clips with
  native 8 kHz and 2–15 second audio. These are local preparation counts, not
  ASR measurements. The audit sample contains 30 Dev rows with blank
  `reference_ok`; no GramVaani results have been run.
- Team audio is present as 54 WAVs. The completed human review sheet is named
  `voice_group_review_FILLED.csv` rather than the prompt's expected
  `voice_group_review.csv`. Explicit validation of that file found 54 rows,
  zero blockers, and synthetic TTS source types. It was not edited; the
  importer was given the filled filename explicitly.
- `data/` is currently wholly ignored by Git. The work order requires only
  team CSVs and aggregate `data/PROVENANCE.md` to be exceptions; audio and raw
  third-party files must stay ignored.
- The locally prepared DEMAND inventory contains 2,400 ten-second segments
  (480 each for five noise types; 400 minutes). The builder verifies the local
  license manifest and uses only Dev segments for Dev mixtures.

## Verified Gnani documentation facts

Checked the current official STT overview, REST, WebSocket, and Prisma v3.0
documentation on 2026-11-14:

- REST accepts recordings up to 30 seconds; both Prisma v2.5 and v3.0 use
  `/stt/v3`. `indic-auto`, `codemix`, and context hints are documented for v3.0
  REST. v2.5 automatic-language support is not listed.
- REST context hints are model-specific form fields; no REST VAD tuning
  parameters are documented. Do not send WebSocket VAD settings on REST calls.
- Streaming VAD uses WebSocket upgrade headers, which must be checked against
  the current exact names before exposing CLI flags.
- Existing code keeps the API key in the server-side client and does not
  expose it through the frontend contract.

## Work-order implementation sequence and non-claims

1. **Audit (complete):** this document records existing interfaces, C2's
   formula, reuse opportunities, and the newly observed data blockers.
2. **WP-D1–D5 (P0):** audit and validate the team files without guessing human
   labels; import team recordings only after review completion; import listed
   noise with license and disjoint time segments; build balanced mixtures;
   preserve GramVaani constraints.
3. **WP0/WP5 (P0):** add fake-client diagnosis and budget planning, cluster-aware
   evaluation, reports, and a freeze/test guard. No paid benchmarks or held-out
   run by the agent.
4. **WP1/WP2 (P1):** only after P0 tooling, add a cited WADA-SNR validation
   path and an isolated C2 OA experiment plan/sweep. Keep routing disabled for
   unvalidated estimates.
5. **WP3/WP6 (P2):** only implement diagnosed REST/streaming levers and minimal
   UI updates; keep output conditions separate.
6. **WP4/C3 (P3/optional):** defer paper-based DSP and optional dependency work
   unless earlier evidence and remaining time justify them.

No Prisma hypothesis is a reference. There are no held-out measurements and
no WER gain is claimed. No real Gnani request is needed to build or test the
tooling; any later live verification must remain within the 20-call ceiling.

## Implementation update

The initial audit above describes the pre-work state. The P0 changes below
extend it; they do not represent ASR measurements.

- Added the reviewed team-data audit/check/import path, DEMAND licensed-noise
  segmenter, balanced mixture builder, manifest selector, hardest-dev selector,
  aggregate data summary, and source-type-aware provenance. The review sheet
  is complete; 54 synthetic clips were imported locally and 62 Dev mixtures
  were built from 31 independent Dev source clips.
- Added C0-only diagnostic tooling with v2.5/explicit-language v3.0/v3.0
  `indic-auto` conditions, `verbatim` format, a fake-client test, dry-run call
  plan, explicit live-budget confirmation, separate output files per condition,
  and failure labels. A reproducible 60-clip GramVaani Dev sample plans 180
  requests and 26.829 audio minutes. No live diagnostic was run.
- Added a streaming-only WebSocket clip utility. It resamples to 8 kHz mono,
  sends 1,024-byte PCM16 frames at 64 ms cadence, adds trailing silence, accepts
  documented VAD headers, and reports event/latency metadata without logging
  transcript text. No WebSocket run was made.
- Added source-cluster bootstrap (`speaker_id` when confirmed, otherwise
  `source_clip_id`), `n_source_clips`, 5 dB SNR bins, source-type breakdown,
  `UNDERPOWERED` suppression below 20 independent source clips, API-call
  counts, and result failure-label counts. The short-output threshold remains
  explicitly unfitted and cannot be used for routing.
- Fixed the router safety gate: an unfitted router or unvalidated SNR
  estimator now routes to C0, even when placeholder rules would select an
  enhanced candidate. Router fitting is blocked until estimator validation is
  recorded in `config/snr_estimators.json`.
- Added development findings generation, a C2-only OA candidate sweep, and
  `docs/experiment-plan.md`. The OA sweep changes only lambda and records the
  unfitted state. Candidate manifests were generated locally, but no candidate
  ASR sweep or benchmark has been run.
- Added code/config hash freezing, held-out candidate-generation checks, and
  a stateful one-time frozen test runner. Direct `run_baseline.py` calls for
  test rows are rejected outside that runner. No frozen config was generated.
- Added CSV/JSON result export and source-type evaluation API fields. No
  workstation UI behavior was changed.

Local metadata summary (not ASR results): GramVaani has 1,132 Dev clips
(175.813 minutes) and 560 Eval clips (89.849 minutes), with no speaker IDs.
The locally prepared DEMAND inventory contains 2,400 ten-second segments
(480 each for five noise types; 400 minutes). The local aggregate
`bharatnoise_api/data/PROVENANCE.md` contains only aggregate statistics.

Still incomplete: TTS engine/provider provenance; WADA-SNR implementation and
validation; a validated SNR estimator; diagnosis-derived REST chunking/language
fallback decisions; measured C0 diagnosis; measured development comparisons;
and any held-out run. No values are selected, the router remains unfitted, and
no WER improvement or held-out result is claimed. See
[`RUNBOOK.md`](./RUNBOOK.md) for the next exact steps and budget gates.
