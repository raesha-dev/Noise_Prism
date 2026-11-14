# Development experiment plan

## Status and data gates

The reviewed team sheet is
`bharatnoise_api/data/team/voice_group_review_FILLED.csv`. Validation found
54 reviewed rows and zero blockers. All are synthetic TTS; split assignment is
by confirmed synthetic voice across languages. There are 31 independent Dev
source clips and 23 Test source clips. Team Test has not been used.

The Dev-only build contains 62 balanced mixtures (two per Dev source clip),
three languages, five DEMAND noise types, and nominal SNRs of -5, 0, and 5 dB.
These are synthetic-speech mixtures and must never be pooled with GramVaani.
No Prisma result files exist and no paid request has been made.

The 60-clip GramVaani Dev sample is reproducible with seed 4187. It plans 180
maximum requests across the three diagnostic conditions and 26.829 audio
minutes. This is a dry run only. The 60-row sample was drawn only from the
organizer's Dev split; the Eval split remains untouched.

## C0 diagnosis

Run the 60 selected GramVaani Dev clips as separate conditions, all with
`format=verbatim`:

1. A: v2.5 with the manifest language.
2. B: v3.0 with the manifest language.
3. C: v3.0 with `indic-auto`.

The result CSV records requested/detected language, output status, word count,
truncation, and reference-based WER/CER where available. It does not store
transcript text. Keep each condition in its own file. Do not select DSP or
fallback behavior until these outputs have been reviewed.

## Fallback simulation gates

If condition files are later provided, an offline simulation may use only the
output symptoms EMPTY, TOO_SHORT, and LANGUAGE_MISMATCH to choose the first
usable condition in a fixed order. It may calculate WER only after a condition
has been selected; reference values must never influence the selection. Do not
deploy a fallback chain until its symptom counts and call/latency cost are
measured on Dev.

## Observation-addition sweep

C2 currently performs its configured enhancement and `enhanced + lambda *
original` addition with shared peak limiting. Its parameters are unfitted.
The separate C2B/C2C simplified spectral experiment is not a substitute for
an isolated C2 OA experiment.

After C0 Dev results and a repeat of condition A exist:

1. Select the 20 hardest development mixtures using only C0 Dev results.
2. Repeat C0 on the same Dev clips with a distinct run ID to measure
   run-to-run variation.
3. Test lambda values `{0, 0.05, 0.1, 0.2, 0.3, 0.4}` on those same 20 clips;
   change only lambda.
4. Adopt a value only if paired WER beats C0 by more than measured repeat-run
   variation and the source-cluster bootstrap 95% confidence interval excludes
   zero. Otherwise select no lambda.
5. Run a selected value on all Dev mixtures and freeze the code/configuration
   hash before any held-out evaluation.

Do not run the test section until after freeze; the owner runs it once. Any
stratum with fewer than 20 independent source clips is `UNDERPOWERED` and has
no comparative claim. The current stationary-noise estimate is unvalidated,
so do not use it to route. WADA-SNR remains unimplemented pending equation
verification.

## No current accuracy claim

No Prisma benchmark or repeated baseline exists. WER/CER, confidence
intervals, API run-to-run noise, latency, failures, OA selection, and held-out
score are not measured. Current routing remains C0.
