# BharatNoise Audio Workstation

## Build
- Replace the home placeholder with a responsive workstation matching the supplied dark dashboard reference, including Audio Studio, C0–C3 tracks, enhancement pipeline, speech analysis, transcript comparison, evaluation, live demo, and experiment history.
- Add a working light/dark theme, accessible controls, audio upload/record/playback, candidate selection, Fast/Max modes, and clear demo-data labeling so sample metrics are not represented as verified results.
- Implement a browser-side adapter for a configurable FastAPI-compatible proxy, with connection testing and documented request/response expectations. Keep the UI usable in demo mode when no service is configured.
- Keep live monitoring explicitly demo-only and design its proxy integration around 8 kHz mono PCM16, 1,024-byte frames at 64 ms cadence, and the requested telephony headers.

## Technical details
- Use the existing TanStack Start/React/Tailwind setup and semantic design tokens; do not add a second application framework.
- The preview has no connected Gnani service or API credentials. The UI can connect when a compatible proxy URL is configured, but Gnani processing and real benchmark results cannot be verified in this project turn.
- Verify the public workstation in the browser, exercise its key controls, run relevant project tests, and check the latest preview build status.
