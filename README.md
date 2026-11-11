# Remix of BharatNoise Audio Studio

Reference pic...BharatNoise | AI Audio Workstation







The mockup includes the Audio Studio, C0–C3 waveform tracks, enhancement pipeline, speech analysis, transcript comparison, evaluation dashboard, and live monitor.



Also include light and dark theme., all buttons functioning successfully 



Keep it entirely end to end integrable with backend which uses gnani ai modules and flask/fast api for all the Features: audio clip input Each clip goes through five stages.







Ingest. Convert to 8 kHz mono PCM16. For synthetic data, mix noise first and apply the telephone codec last (band-limit plus G.711 via ffmpeg; check your build for AMR-NB).







Analyse. Run Silero VAD (supports 8 kHz in recent versions; verify yours), then estimate the noise floor from non-speech frames and a per-clip SNR.







Make candidates.







C0: the original audio.







C1: hygiene only: DC removal, 300–3400 Hz band-limit, 50 Hz mains-hum notch and gain normalisation.







C2: conservative enhancement (Wiener or spectral gating) blended with the original, y = ê + λ·x. Fit λ on the dev set as a function of estimated SNR.







C3: DeepFilterNet via 8→48→8 kHz, a candidate for ASR and also the listening version for the demo.







Speech-preservation guard. On VAD-speech frames, compare speech-band energy and log-spectral distance between the original and enhanced audio. If the enhancer is eating speech, raise λ or fall back to C1. This is your version of ASPC. Check that the guard score actually correlates with ΔWER. If it doesn’t, don’t claim it.







Two accuracy modes.







Fast (1 API call): thresholds on estimated SNR pick one candidate.







Max accuracy (3–4 calls): transcribe C0, C1 and C2, plus v2.5 versus v3.0, then pick the hypothesis closest to all the others (smallest summed pairwise WER). This is minimum-Bayes-risk selection, and it needs no confidence scores. Report the 3× cost in calls and latency honestly.







Live mode is demo-only. It streams exactly 1,024-byte frames at 64 ms cadence with x-sample-rate: 8000 and the telephony VAD preset, through your proxy. It uses only the cheap candidates.







If nothing beats C0 on the dev set, your story becomes: “we measured that aggressive denoising hurts Prisma by X; our guard and consensus recover Y.” That is credible and still ships. Don’t claim denoising improves transcription unless your numbers

This project was built with [Lovable](https://lovable.dev).

## Build with Lovable

Continue developing this project in the [Lovable editor](https://lovable.dev/projects/f413851d-046f-4dda-9243-fb8c33a18ef5).

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: every change made in Lovable is committed straight to this repository.
- **Full ownership**: this code is yours. Push to `main` on GitHub and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```
