# BharatNoise final work order for Copilot

Written for: Saturday 14 Nov 2026. Gnani's submission cutoff is Sunday 15 Nov 2026, 18:00 IST, so about 36 hours remain.

## ROLE

You are a senior speech-processing and Python engineer. Improve the EXISTING BharatNoise app (React/TypeScript frontend, FastAPI backend in `bharatnoise_api/`, live Gnani Prisma integration). Do not rebuild or restructure it. Extend working modules, keep every existing test passing, never weaken, skip or delete a test, and never use eslint-disable or type-ignore comments to silence problems.

## GOAL

Gnani's Noise Canceller Award rewards the build that performs best on messy, noisy, real telephonic audio. Today the app transcribes clean-to-mid audio but fails on very messy audio. Find out WHY, then add only changes that measurably reduce word error rate (WER) on held-out data. Optimise ASR accuracy, not how clean the audio sounds.

## PRIORITIES (about 36 hours; do them in this order)

- P0: AUDIT, WP-D1 to WP-D3 (data), WP0 (diagnosis tooling), WP5 (evaluation tooling and freeze guard), RUNBOOK.
- P1: WP1 (SNR estimate fix), WP2 (observation addition).
- P2: WP3 (non-enhancement levers, only those the diagnosis justifies), WP6 (minimal UI).
- P3, cut by default: WP4 (MCRA/OM-LSA/IMCRA), C3 DeepFilterNet.

Cut order if behind: C3, WP4, WP3, WP6 extras. After each WP print: files changed, tests run, exit codes, what is not done. If a test still fails after two fix attempts, stop that WP, report it, and continue.

## RULES

1. Never fabricate numbers, transcripts or results. Every displayed value comes from a results file or a live computation.
2. Never use a Prisma transcript as a reference for Prisma. References come only from the human-authored script or an annotated dataset. An empty transcript is never "correct".
3. The test split stays untouched until parameters are frozen (WP5).
4. Gnani REST calls stay serial through the existing worker with the existing cache and backoff. `GNANI_API_KEY` and `HF_TOKEN` come from the environment only and are never logged, returned or committed.
5. Pin `language_code` and `format=verbatim` across candidates in every benchmark.
6. Existing candidates C0 (original), C1 (hygiene), C2 (Wiener-style) and optional C3 keep working. C0 is never modified.
7. Mock-mode behaviour (labelled transcripts, suppressed scores, `results_mock/`) stays exactly as is.
8. PAID CALLS: you may make at most 20 real Gnani calls yourself, only to verify wiring. Do NOT run benchmarks, sweeps or diagnostics at scale. Build the tooling, test it with fake clients, and write the exact commands, with planned calls and audio minutes (use the existing plan_budget), into `docs/RUNBOOK.md` for me to run. I will paste the results back for you to analyse.
9. DATA RULES: no real customer calls or personal data anywhere. Never commit audio, tokens or third-party transcripts. GramVaani audio and transcripts never appear in the repo, in `samples/`, in the UI or in any demo; only aggregate numbers may be published. Speaker IDs are pseudonyms (`spk_a`, `spk_b`), never real names.
10. If you cannot verify a constant or equation from a cited paper, put it in config marked "unverified", say so in the report, and do not invent it.
11. NEVER infer speakers, splits or source types automatically. Those come only from the human-filled review sheet. If it is incomplete, print the blocking rows and continue with work that does not depend on it.
12. Commit locally after each WP. Never claim a check passed unless you ran it and saw exit code 0.

## GNANI FACTS (re-read https://docs.gnani.ai/api/STT/ to confirm before relying on them)

- REST POST `/stt/v3`: clips of 30 s or less, one active request per account (429 RATE_LIMITED), the server resamples to 16 kHz, no VAD parameters are documented for REST, v2.5 responses may lack latency fields (handle null), identical audio can differ slightly between calls.
- Streaming: exact 1,024-byte frames at 64 ms cadence; VAD tuning is by upgrade headers (x-vad-threshold, x-min-speech-ms, x-min-silence-ms; confirm exact names in the docs).
- `indic-auto`: the docs disagree on whether v2.5 accepts it. Test it and record the result; never assume.
- Credits: 5,000; the per-minute rate is unknown, so it is measured.
- Licences: IndicVoices is CC BY 4.0 and gated on Hugging Face. GramVaani is free for academic use only. The DEMAND record lists both CC BY 4.0 and CC BY-SA 3.0; assume the stricter BY-SA 3.0 (credit it, do not redistribute noise files or mixtures).

## VERIFIED FACTS ABOUT THE TEAM DATASET (measured from the uploaded files; do not re-derive, but your validator must re-check them)

Files, to be placed at:
- `bharatnoise_api/data/team/script_with_voice_filenames.csv` (unchanged, source of the sentence text)
- `bharatnoise_api/data/team/voice_group_review.csv` (the extended review sheet, filled in by a human; the single source of truth for speaker, split and source type)
- `bharatnoise_api/data/team/audio/*.wav` (54 files)

Facts:
- 54 rows: 18 sentences x 3 languages (`hi-IN`, `kn-IN`, `en-IN`). IDs are `hi_01`..`hi_18`, `kn_01`..`kn_18`, `en_01`..`en_18`. The two-digit number is the same sentence in all three languages.
- `script_with_voice_filenames.csv` columns: id, language, text, filename, speaker_id (empty), split (empty), voice_group_suggestion, voice_group_status (all "PROVISIONAL: acoustic candidate only; listen and confirm before using as speaker_id").
- `voice_group_review.csv` columns (extended): id, language, filename, reference_text, voice_group_suggestion, speaker_id_confirmed, split, source_type, quiet_room, notes, then measured advisory columns native_sample_rate_hz, duration_s, noise_floor_db, digital_silence_frac, approx_pitch_hz, likely_synthetic_advisory. The five human columns (speaker_id_confirmed, split, source_type, quiet_room, notes) start empty.
- The two CSVs agree on id, language, filename, text, and voice group. All 54 filenames exist exactly in the audio folder; no extra audio files.
- Filenames are the first ~20 characters of the sentence plus `.wav`, with spaces, and with Devanagari/Kannada characters for hi and kn. Punctuation is dropped (hi_18, kn_18, en_18 lack the comma). Therefore map audio to rows ONLY through the CSV `filename` column, comparing Unicode-NFC-normalised strings on both sides (macOS may list filenames in NFD). Never map by a naming pattern.
- Audio: 54 mono 16-bit WAVs, 1.5 to 4.4 s, about 2.6 minutes in total. Native sample rates are mixed: 48,000 Hz (38 files), 24,000 Hz (13), 22,050 Hz (3).
- Voice groups are PROVISIONAL and per-language: 13 groups (en 5, hi 5, kn 3). The labels cannot show that one person spoke in two languages. Three groups mix native sample rates (en_candidate_03, hi_candidate_02, kn_candidate_03), which usually means different sources. Speaker identity is therefore UNRESOLVED.
- 29 of 54 files have exact digital silence (noise floor at the measurement limit, about 22% of samples exactly zero, stationary SNR estimates around 119 dB). That is the signature of synthetic speech or heavy gating, not of a microphone in a room. 16 files have microphone-like noise floors (-47 to -74 dB). Treat the source of every file as UNKNOWN until the human fills `source_type`.
- No staged noisy phone-call recordings are present.
- Script text contains no digits or currency symbols (numbers are spelled out) and no Latin letters in the hi and kn sentences.
- Only 54 base utterances exist. Mixtures built from the same base clip are NOT independent samples.

## AUDIT FIRST (no edits)

Read the repo, especially `bharatnoise_api/pipeline.py`, `server.py`, `audio/`, `gnani_client.py`, `metrics.py`, `compare.py`, `run_baseline.py`, `scripts/`, `config/`, the existing tests and the workstation components. Write `docs/improvement-plan.md`: existing interfaces, how C2's blending is computed (already y = enhanced + lambda*original, or a convex mix?), which items below already exist, contradictions found. Do not re-implement anything that exists.

## WP-D DATA (P0, do first; tests use synthetic fixtures and no network)

Layout under `bharatnoise_api/data/`: `raw/`, `audio/`, `manifests/`, `noise/`, `team/`. Gitignore everything under `data/` except `data/team/*.csv` and `data/PROVENANCE.md` (never the audio).

Manifest columns for every source: id, source_clip_id, audio_path, language, reference, speaker_id, group, source, source_type, license, reference_provenance, native_sample_rate, split, split_basis, clean_reference_available, noise_type, snr_db_nominal, snr_db_measured, snr_basis.

### WP-D1 Team set: audit, validator, importer

1. `scripts/audit_team_audio.py` (offline): for every audio file report native sample rate, duration, noise floor, fraction of exactly-zero samples, an approximate pitch, and advisory flags. Write `data/manifests/team_audit.csv`. The flags are advisory and never decide anything.
2. `scripts/check_team_set.py` (offline, exit code 1 while incomplete, with row-level messages):
   - Both CSVs: same ids; same language; same filename; `text` equals `reference_text` after NFC; language prefix of the id matches `language`; ids unique.
   - Every filename resolves to exactly one audio file by NFC-normalised comparison; no unreferenced audio files.
   - Review sheet required values: `speaker_id_confirmed` matches `^spk_[a-z0-9]+$` and is a GLOBAL pseudonym across languages (the same person keeps the same ID in every language); `split` in {dev, test}; `source_type` in {human_recording, synthetic_tts}; `quiet_room` in {yes, no} when source_type is human_recording.
   - Every speaker belongs to exactly one split; both splits non-empty; each language has at least 5 clips per split, otherwise print UNDERPOWERED.
   - Print a table of clip counts by split x language x speaker x source_type.
   - If fewer than two distinct confirmed speakers exist, do not invent any: offer `--allow-sentence-split`, which assigns the split by the two-digit sentence number so that all three languages' versions of a sentence stay together, sets `split_basis="sentence_disjoint_NOT_speaker_disjoint"`, and prints that label in every report. A manual `split` that breaks speaker-disjointness is an error unless that flag is used.
3. `scripts/import_team_recordings.py`: read both CSVs, resample each file to 8 kHz mono WAV with an anti-aliased resampler (`scipy.signal.resample_poly`), record `native_sample_rate`, apply no loudness normalisation. Reference = the script text in NFC, `reference_provenance="team script"`, `source_clip_id` = the row id. `clean_reference_available` is true only for `synthetic_tts`, or `human_recording` with `quiet_room=yes`. Rows with a group value `staged_call` (noisy phone recordings, if added later) get `clean_reference_available=false`. Write the manifest for the 54 clean clips with group `clean_control`; these are scored separately (see WP0) and are never pooled with noisy results.
4. All reports state the `source_type` mix. If clips are `synthetic_tts`, say "synthetic speech" everywhere (README, UI, reports) and never call them real recordings or real telephone calls.

### WP-D2 Noise

`scripts/import_noise.py`: sources are `data/raw/demand` (STRAFFIC, PCAFETER, PSTATION, TBUS, DLIVING, 16 kHz, extracted; at most 2 channels per scene) and `data/team/noise/*.wav` if present. Skip any missing source and print which noise types exist; warn if fewer than 2 types. Resample to 8 kHz mono and cut into 10 s segments. Assign the first 60% of each recording's time to dev and the last 40% to test so no noise segment appears in both. Write `data/noise/<type>/<split>/*.wav` and `data/noise/LICENSES.csv` (file, type, source, license, note). `build_eval_set.py` refuses noise files not listed there.

### WP-D3 Evaluation-set builder

Extend `scripts/build_eval_set.py` with a BALANCED design: for each base clip with `clean_reference_available=true`, create `--mixtures-per-clip` mixtures (default 2) with a deterministic assignment of (noise type, SNR in {-5, 0, 5} dB) so that every cell is used equally often (within one clip) inside each split and language. Dev base clips use dev noise; test base clips use test noise. Apply the existing telephone-channel simulation AFTER mixing. Record `source_clip_id`, `noise_type`, `snr_db_nominal`, `snr_db_measured` (only for clean_reference_available=true), `snr_basis` ("measured_vs_clean" or "nominal"). Compute SNR improvement and SI-SDR ONLY for clean_reference_available=true mixtures. Write `build_log.json` (seeds, sources, SNRs). Print the planned call count for the candidate list and the number of INDEPENDENT source clips per split (it is not the number of mixtures).

### WP-D4 GramVaani (optional; implement only if `data/raw/gramvaani` exists)

`scripts/import_gramvaani.py`: first inspect the extracted GV_Dev_5h, GV_Eval_3h and Metadata folders and document their real transcript and metadata formats in `docs/data-notes.md`. Decode MP3 with soundfile (verify MP3 support; otherwise print the exact error and a fix). Read each file's native sample rate. Keep clips that are native 8 kHz, 2 to 15 s, with a non-empty reference after normalisation. Convert to 8 kHz mono WAV. The organisers' Dev folder is split "dev" and Eval is split "test". Read speaker IDs from metadata if present, otherwise leave blank and warn. Write `data/manifests/gramvaani_audit.csv` with 30 random clips and a blank `reference_ok` column for manual listening; exclude any clip marked `reference_ok=0`. Mark `clean_reference_available=false`. Never pool with synthetic results.

### WP-D5 IndicVoices (optional; implement only if `HF_TOKEN` is set)

`scripts/import_indicvoices.py`: `datasets.load_dataset("ai4bharat/IndicVoices", <language>, split="valid", streaming=True)`. NEVER download the full dataset. Inspect one sample to choose the verbatim-style text field and the audio column; document both. Read audio with `Audio(decode=False)` and decode the bytes with soundfile so no torchcodec or ffmpeg is needed. Take N per language (default 40; hi and kn), 3 to 15 s, non-empty text. Use a speaker field only if one exists; otherwise leave `speaker_id` blank and set `split_basis="clip_index_not_speaker_disjoint"`. Mark `clean_reference_available=false`.

### Data summary

Print a summary (counts per source, source_type, language, split, speaker; audio minutes; independent source clips) and generate `data/PROVENANCE.md` from the manifests. Real recordings (GramVaani, staged calls) and synthetic material (synthetic_tts, synthetic mixtures) are reported separately everywhere; never pooled.

## WP0 DIAGNOSE WHY MESSY AUDIO FAILS (P0, no new DSP)

Create `scripts/diagnose_clips.py`. Input: a manifest. For each clip run C0 only and record: word count, words per second of total audio, empty flag, truncated flag, detected_language_code (v3.0 only), requested language, and a failure label: EMPTY | LANG_MISMATCH (detected differs from requested, v3.0 only) | TOO_SHORT (words per second below a configurable, unfitted threshold; label it a heuristic) | OK. Support these conditions, each recorded separately: (a) v2.5 with the correct language; (b) v3.0 with the correct language; (c) v3.0 with `indic-auto` (only where the docs allow it; report any rejection); (d) the chunked variant from WP3 if implemented. Add per-clip WER when references exist. Provide `--limit` and a dry-run that only prints planned calls.

Required comparison: the `clean_control` clips (54 clean clips, C0) versus the noisy dev mixtures of the same base clips (C0), per language. The clean WER is the ceiling; if a language already fails on clean clips, noise is not the cause. This run also confirms the filename-to-text mapping (a high clean WER on specific rows means a mismatch or misread to check by listening). Report it as a data-integrity check; it is never used as a reference.

Create `scripts/stream_clip.py`: stream one WAV through the real WebSocket at exact 64 ms cadence with CONFIGURABLE VAD headers (only the documented names), to compare default versus more sensitive VAD on failing clips. Report it as streaming-only; REST has no documented VAD parameters.

Backend and UI: when a v3.0 job's detected language differs from the requested one, return `language_mismatch=true` and show a visible warning with a one-click re-run in the detected language. Add an "Auto language (v3.0)" option only if the docs support it.

After I run it and paste the results, write `docs/diagnosis.md` from the output: failure-label counts per condition and per language, and state which failure type dominates.

## WP5 EVALUATION (P0; reuse metrics.py, compare.py, run_baseline.py, aggregate_results.py)

Do NOT run the benchmark yourself. Write, in order, into `docs/RUNBOOK.md` the exact commands for me to run: clean-control baseline; dev runs per candidate and model; a repeat run of C0 (run_id 1) to measure the API's run-to-run noise floor; the OA sweep on the 20 hardest dev mixtures only; the single test run after freezing. Each step lists planned calls and audio minutes from plan_budget.

Statistics, because there are only 54 base utterances:
- Bootstrap by CLUSTER: resample `source_clip_id` (and speaker when confirmed) so mixtures of the same clip are not treated as independent. Print `n_source_clips` beside every interval.
- Any stratum (language, noise type, SNR bin, source_type) with fewer than 20 source clips is labelled UNDERPOWERED: print its numbers but make no win/loss or "improvement" claim for it.
- Report real and synthetic results separately; never pool.

After dev results exist, generate `docs/dev-findings.md` by script: which candidates beat C0 beyond the measured noise floor, by SNR bin, noise type, language and source_type, plus the failure-label distribution per candidate (do gains come from recovering EMPTY or TOO_SHORT outputs?).

Freeze guard: `scripts/freeze_config.py` writes `config/frozen.json` with the chosen lambda, suppression parameters, router thresholds and a hash of the code and config. The test runner refuses to run without a matching `frozen.json`. The test split is run ONCE, by me.

Reports: per-recording WER and CER; corpus WER and CER; cluster-bootstrap 95% CI of the change versus C0; win/tie/loss; median per-clip change; latency p50 and p95; API calls. SNR improvement and SI-SDR only on mixtures with `clean_reference_available=true`.

Decision gate: if nothing beats C0 beyond the noise floor on dev, the final system routes to C0 (or to whichever non-enhancement lever helped) and the findings say so. Do not keep tuning past the time-box.

Speech-preservation diagnostics: keep them; compute their correlation with per-clip change in WER on dev; call them validated only if held-out data supports it; never call them novel.

## WP1 FIX THE NOISE ESTIMATE (P1)

`snr_db_est` compares speech-frame energy with the quietest 20% of frames, so it misses noise that overlaps speech (it showed 30.3 dB on a messy clip). Rename it `stationary_snr_db_est` and relabel it in the UI "stationary-noise estimate". Add `wada_snr_db` (WADA-SNR, Kim and Stern, Interspeech 2008). Write `scripts/validate_snr_estimators.py`: build mixtures at -5, 0, 5, 10, 15 dB from clips with `clean_reference_available=true`, report mean absolute error and bias per estimator per noise type. Routing may use an estimator only if it validates; otherwise remove SNR from the router and say so. Never present an SNR as "clarity" unless validated. In Max mode also compute agreement (mean pairwise word edit distance between candidate hypotheses) with a low-agreement warning whose threshold is unfitted and labelled so.

## WP2 OBSERVATION ADDITION SWEEP ON EXISTING C2 (P1)

Reference: Iwamoto et al. 2022, arXiv 2201.06685. Implement `observation_add(enhanced, original, lam) = enhanced + lam * original` as a separate deterministic function (if C2's blend already equals this, reuse it and document that). Apply the SAME final safety policy to every variant: scale down only if the peak would exceed 0.99, record the applied gain, and add no loudness normalisation that differs between variants. Generate candidates named like `C2+OA(0.10)` for lam in {0, 0.05, 0.1, 0.2, 0.3, 0.4} with a script, not six UI items; the UI exposes one lambda control mapped to real backend parameters. Write `docs/experiment-plan.md` BEFORE any dev run with the selection rule: choose a lambda only if it beats C0 on dev by more than the measured API run-to-run difference with the cluster-bootstrap 95% interval excluding zero; otherwise choose none. Sweep lambda only on the 20 hardest dev mixtures (lowest C0 quality), then run the chosen value on all of dev.

## WP3 NON-ENHANCEMENT LEVERS (P2; implement each only if WP0 shows it matters; each is a flagged option and a separately recorded condition)

a) Chunking: only relevant for long clips (the team clips are 1.5 to 4.4 s, so skip it for them). For clips over a configurable length, split at low-energy points into windows of at most 10 s with 0.4 s overlap, transcribe sequentially, stitch by removing duplicated overlap tokens (bounded longest suffix/prefix token match). Never exceed the 30 s REST limit.
b) Correct-language and auto-language handling from WP0.
c) Prisma v3.0 context hints (keyterms, topic, previous_text): expose them ONLY if the REST docs document them, and test with and without, because the docs note context can lower accuracy.
d) The streaming-VAD result from WP0, reported separately as streaming-only.

## WP6 MINIMAL UI (P2)

Show per-candidate transcripts (C0, C1, C2, C2B, C2C when they exist, C3 when available, and any lever condition), per-candidate WER and CER when a human reference is typed, word-level alignment highlighting, failure labels, the language-mismatch warning, and the agreement value. Add an enhancement-method selector and one lambda control only for implemented parameters. Do not hide failed, empty or truncated candidates. Extend the evaluation panel to read measured results (candidate comparison, SNR and noise breakdowns, source_type, API calls, latency, confidence intervals with `n_source_clips`, win/tie/loss) from the results API with explicit empty states, and add a CSV and JSON export endpoint for any results file. Mark UNDERPOWERED strata. No decorative controls and no dataset-import UI (CLI only).

## WP4 MCRA + OM-LSA, THEN IMCRA (P3, cut by default; start only if `docs/diagnosis.md` shows that wrong-word errors, not EMPTY, LANG_MISMATCH or TOO_SHORT, dominate, and WP2 is finished)

Do not overwrite C2. Register C2B (noise estimation + OM-LSA gain) and C2C (C2B + observation addition). Implement the published algorithms from their equations and cite equation numbers in docstrings:
- Cohen and Berdugo 2001, Signal Processing 81(11), "Speech enhancement for non-stationary noise environments" (MCRA and OM-LSA, https://doi.org/10.1016/S0165-1684(01)00128-1).
- Cohen 2003, IEEE Trans. Speech Audio Process. 11(5), "Noise spectrum estimation in adverse environments: improved minima controlled recursive averaging" (IMCRA, https://doi.org/10.1109/TSA.2003.811544).

Step A: MCRA + OM-LSA first. Step B: IMCRA only if Step A passes all its tests with time left. Never label something IMCRA or OM-LSA unless it follows the paper; name any simplification honestly. Requirements: mono 8 kHz in and out; sample count and alignment preserved; deterministic; no NaN or inf; no unintended clipping; correct on digital silence, very low energy, and recordings with no clear speech pauses; every parameter in config with its source (paper or "unverified"). Do not copy third-party code; verify any library's licence and real 8 kHz behaviour first. Tests: noise PSD converges on stationary white noise and tracks a step change; speech-presence probability is high on strong tone bursts and low on noise only; gain stays within [floor, 1]; ISTFT with unit gain returns the input within tolerance; silence gives finite output; determinism; length preservation. State in the report that these methods assume noise varies slower than speech, so gains on babble and horns are not guaranteed.

## WP7 VERIFY AND REPORT

Run pytest, Vitest, the frontend E2E, tsc and lint. All must pass and existing test counts must not drop. Confirm C0, C1, C2 and the live Prisma path still work (at most 20 real calls). Print: files changed, new tests, a table of every command with its exit code, measured dev results (from files only), every unverified constant, what you did not implement, and the blocking items still waiting on me (for example an incomplete review sheet).

## OPTIONAL, LAST (30 minutes maximum): C3 DeepFilterNet

Report the exact pip error for "No module named df". If the cause is the Python version, document a separate Python 3.12 environment (torch and torchaudio first, then deepfilternet) without breaking the default one. If it does not work within the time-box, leave C3 unavailable and say so.
