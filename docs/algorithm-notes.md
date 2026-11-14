# Experimental spectral candidates: implementation notes

## What is implemented

C2B uses a 256-sample Hann STFT window and a 64-sample hop at 8 kHz. It
smooths a one-sided periodogram over time, tracks finite-window local minima
using periodically updated blocks, estimates speech presence from ratios to
those minima in two passes, and updates a frequency-dependent noise estimate
using the estimated speech-absence probability. A speech-presence-weighted
log-spectral gain is applied, floored by configuration, then inverse-STFT
reconstruction is trimmed or padded to the original sample count. The backend
uses the existing VAD mask to smoothly attenuate non-speech regions and raise
detected speech toward a configured RMS target, with a maximum gain and peak
limit. The speech target, gain cap, and non-speech gain are placeholders, not
fitted values. This stage can affect noise within speech and does not infer
microphone distance.

C2C adds the original signal to the enhanced signal as
`enhanced + lambda * original`. The configured sweep shares one peak-limiter
scale across its coefficients. It is not convex interpolation. The coefficient
and all spectral and voice-leveling settings are experimental and
`fitted: false`; adding original audio can restore both speech and noise.

## Fidelity and known departures

The code is research-inspired, but its fidelity to Cohen and Berdugo (2001)
and Cohen (2003) has **not been verified** against the papers' full equations.
The available DOI pages did not provide full-text equation verification.
Consequently, do not describe these candidates as implementations of those
named algorithms.

Observable choices in this implementation that require equation-by-equation
verification include:

- The finite moving minimum is built from periodic block minima held in a
  bounded `deque`; it is not established to match the papers' exact recursive
  minimum-statistics update.
- Speech-presence probabilities use clipped linear ratios to tracked minima,
  a fixed three-tap frequency smoother, and the maximum of the two passes.
- Noise PSD adaptation uses a single configured smoothing coefficient
  weighted by estimated speech absence.
- The log-spectral gain uses an exponential-integral expression, a
  decision-directed prior, a posterior speech-presence logistic, fixed gain
  smoothing, and a configured floor. Constants and probability equations have
  not been independently checked against the source equations.
- STFT windowing, boundary padding, one-sided reconstruction, explicit output
  trimming, and peak limiting are implementation choices; paper equivalence
  is unverified.
- The VAD gate, speech-RMS target, gain cap, and sample-smoothing envelope are
  additional practical stages, not parts of a paper-verified MCRA/OM-LSA
  implementation.

Do not tune or route from these candidates until the development-only
measurement gates in [experiment-plan.md](./experiment-plan.md) pass. No WER
or ASR benefit is currently measured.

## References

- I. Cohen and B. Berdugo, *Speech Enhancement for Non-Stationary Noise
  Environments*, Signal Processing 81(11), 2001,
  [doi:10.1016/S0165-1684(01)00128-1](https://doi.org/10.1016/S0165-1684(01)00128-1).
- I. Cohen, *Noise Spectrum Estimation in Adverse Environments: Improved
  Minima Controlled Recursive Averaging*, IEEE TASLP 11(5), 2003,
  [doi:10.1109/TSA.2003.811544](https://doi.org/10.1109/TSA.2003.811544).
- T. Ochiai et al., *How Bad Are Artifacts? Analyzing the Impact of Speech
  Enhancement Errors on ASR*, Interspeech 2022,
  [arXiv:2201.06685](https://arxiv.org/abs/2201.06685).
