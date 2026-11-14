# Phase 2 audit and execution status

## Existing interfaces and reusable behavior

- `POST /process` and the workstation implement clip processing; `GET /api/config`
  exposes candidate/configuration availability. `POST /api/jobs` uses the
  serial Gnani REST worker. `/ws/live` is a separate streaming proxy.
- `pipeline.py` owns audio decode/resample, analysis, C0/C1/C2 candidates,
  optional C3 availability, guarded candidate processing, and the C0-only
  safety fallback while router parameters or the SNR estimator remain
  unvalidated.
- C2B/C2C are opt-in experimental spectral processing. C2C adds the original
  audio to its processed counterpart. The paper fidelity is unverified; see
  [algorithm-notes.md](./algorithm-notes.md).
- `gnani_client.py` handles serial requests, backoff, caching, and fatal errors.
  `run_baseline.py` records individual condition results; `plan_budget.py`
  computes exact call and duration plans without sending requests.
- `diagnose_clips.py` supports C0 with v2.5/manifest language, v3.0/manifest
  language, and v3.0/`indic-auto`, all using `verbatim`. Its output files are
  separated by condition. Short-output thresholds remain unfitted.
- `aggregate_results.py`, `compare.py`, and `analyze_dev_findings.py` produce
  paired results, confidence intervals, source counts, and failure summaries.
  The report tool must be run only over measured, non-mock result files.
- `freeze_config.py` and `run_frozen_test.py` gate the single held-out run.
  Held-out data are not used in this work order.
- Team review/import, DEMAND licensed-noise segmentation, reproducible
  development sampling, balanced mixture construction, data summaries, and
  candidate-manifest generation are available as CLI tools.

## Work completed in this phase

- Confirmed `voice_group_review_FILLED.csv` has 54 reviewed rows and zero
  blockers. All are synthetic TTS; split assignment is voice-disjoint across
  languages. The importer used this explicit filename and did not read
  advisory audio-analysis columns.
- Imported 54 synthetic speech clips locally and built 62 development-only
  noise mixtures from 31 independent Dev source clips. Held-out team audio was
  not used for mixtures. No third-party ASR call was made.
- Generated C0/C1/C2 Dev candidate files and a reproducible 60-row
  GramVaani-Dev diagnostic manifest (seed 4187). The diagnostic dry run plans
  180 maximum requests and 26.829 audio minutes. No results were generated.
- Repaired relative noise-directory handling in the mixture builder after the
  local build exposed a `Path.relative_to` failure, and made direct
  `diagnose_clips.py` invocation import correctly.
- Updated C2B/C2C user-facing descriptions to identify them as simplified,
  unfitted experiments with unverified paper fidelity.
- Router remains C0-only. WADA-SNR is not implemented because its cited
  equation has not been verified. A local controlled-mixture diagnostic found
  positive bias in the stationary estimate across the tested noise/SNR cells;
  it remains unvalidated and cannot route. No paid calls, benchmark, OA
  selection, or held-out run has been made.

## Contradictions and unresolved decisions

- The owner prompt refers to the canonical `voice_group_review.csv`; the
  completed file in this workspace is named `voice_group_review_FILLED.csv`.
  The completed file was used explicitly and left unchanged.
- Synthetic voices are not human speakers. Their Dev results must be reported
  as synthetic speech and separated from GramVaani. The TTS engine/provider
  and voice names have not been independently recorded.
- GramVaani provides the real-telephone corpus here, but it has no speaker IDs.
  Dev/Eval are organizer splits; no Eval result has been run.
- A correct language hint, `indic-auto`, channel/rate changes, REST chunking,
  and fallback behavior remain hypotheses until the owner runs the C0
  diagnosis. No new REST behavior has been selected.
- There are no benchmark result CSVs. WER/CER, confidence intervals,
  run-to-run variation, latency, failures, and improvement are not measured.
- The 60-row diagnostic plan is a dry-run plan, not authorization to consume
  credits. The owner must first establish the remaining budget and the
  30%-reserve limit in [RUNBOOK.md](./RUNBOOK.md).
