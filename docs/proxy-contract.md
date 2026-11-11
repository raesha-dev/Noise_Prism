# BharatNoise proxy contract

The workstation is a static browser app. It never holds Gnani credentials. All processing goes through
a FastAPI or Flask **proxy** that you run; the proxy calls Gnani AI Prisma with its own key.

Configure the proxy address in the app (Settings → *Proxy base address*) or at build time with
`VITE_BHARATNOISE_API_URL`. Hosted proxies must use HTTPS; `http://localhost` is allowed for development.
The proxy must send CORS headers that allow the site's origin (`GET`, `POST`, `OPTIONS`; WebSocket upgrade on `/live`).

All error responses should be JSON: `{"detail": "human readable message"}` (FastAPI default) or `{"message": "..."}`.

## GET /health

```json
{ "status": "ok", "gnani_configured": true, "version": "0.3.1" }
```

## POST /process  (multipart/form-data)

| field | value |
| --- | --- |
| `file` | the uploaded/recorded clip (any browser format; proxy converts) |
| `mode` | `fast` or `max` |
| `sample_rate` | `8000` — resample to 8 kHz before enhancement and ASR |
| `channels` | `1` |
| `sample_format` | `pcm_s16le` |
| `asr_provider` | `gnani_prisma` |
| `asr_model` | e.g. `prisma-v2.5`, `prisma-v3.0` |
| `candidates` | comma list from `C0,C1,C2,C3` (C3 = DeepFilterNet, optional) |
| `selection` | `snr_router` (Fast: exactly 1 Prisma call) or `consensus` (Max: 3–4 calls, ~3× cost) |
| `compare_models` | `true`/`false` (Max only) |

Five stages per clip: ingest → VAD/SNR analysis → generate C0–C3 → speech-preservation guard → route & transcribe.

Response (snake_case; every field except one of `selected_candidate` / `transcription` / `candidates` is optional):

```json
{
  "request_id": "b1c2…",
  "selected_candidate": "C2",
  "transcription": "haan mujhe woh plan kal tak bhej dena",
  "candidates": {
    "C0": { "transcript": "…", "audio_url": "/audio/b1c2/c0.wav", "peaks": [0.1, 0.4], "guard_score": 1.0 },
    "C1": { "audio_url": "/audio/b1c2/c1.wav", "guard_score": 0.97 },
    "C2": { "transcript": "…", "audio_url": "/audio/b1c2/c2.wav", "guard_score": 0.91 }
  },
  "analysis": {
    "snr_db": 6.4, "noise_floor_db": -48.2, "speech_energy_db": -21.7,
    "vad_segments": [[0.21, 1.84], [2.10, 4.95]],
    "guard_status": "passed", "guard_score": 0.91,
    "spectrum": [0.4, 0.6]
  },
  "evaluation": { "wer": 0.153, "cer": 0.078, "latency_ms": 436, "calls": 1, "note": "WER vs reference in request" },
  "pipeline": ["ingest", "vad_snr", "candidates", "guard", "route:C2"],
  "latency_ms": 1820
}
```

- A candidate may also be a plain string (its transcript).
- `audio_url` may be absolute or relative to the proxy base; the browser fetches it (CORS) to play and draw waveforms.
- `peaks` (0–1, ~320 values) skips client-side decoding.
- WER/CER are fractions (0.153 = 15.3%). Report honestly: if no candidate beats C0, say so.

## GET /evaluation

Labeled dev-set results. Same shape can be imported as a JSON file in the Evaluation lab.

```json
{
  "dataset": "dev-v1 (Hindi telephony)",
  "clip_count": 240,
  "measured_at": "2026-11-10",
  "sections": {
    "comparison": [{ "id": "A", "pipeline": "C0 · Original", "candidate": "C0", "wer": 0.189, "cer": 0.096, "latency_ms": 412 }],
    "ablation":   [{ "id": "A", "pipeline": "Original input", "wer": 0.189 }],
    "snr":        [{ "id": "1", "pipeline": "SNR < 0 dB", "wer": 0.41 }],
    "noise":      [{ "id": "1", "pipeline": "Traffic / babble", "wer": 0.27 }]
  }
}
```

## WS /live  (demo only)

URL: `wss://<proxy>/live?sample_rate=8000&vad_preset=telephony`

1. Client sends one JSON text message:
   `{"type":"config","sample_rate":8000,"channels":1,"encoding":"pcm_s16le","frame_bytes":1024,"frame_ms":64,"vad_preset":"telephony","candidates":["C0","C1","C2"],"demo":true}`
2. Client then sends **binary frames of exactly 1,024 bytes** (512 PCM16 LE samples = 64 ms) every 64 ms.
   Browser DSP (noise suppression, AGC, echo cancellation) is disabled.
3. Browsers cannot attach custom WebSocket headers, so the proxy adds `x-sample-rate: 8000` and the
   telephony VAD preset on its upstream Gnani streaming connection. Use only the cheap candidates (C0–C2).
4. Proxy sends JSON text messages back:
   - `{"type":"partial","text":"haan mujhe"}`
   - `{"type":"final","text":"haan mujhe plan bhej dena","candidate":"C1"}`
   - `{"type":"metrics","latency_ms":182,"snr_db":7.5}` — measured latency; the UI never invents it.
   - `{"type":"error","message":"…"}`

## Minimal FastAPI skeleton

```python
from fastapi import FastAPI, File, Form, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["https://your-site.lovable.app"], allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
def health():
    return {"status": "ok", "gnani_configured": bool(GNANI_KEY)}

@app.post("/process")
async def process(file: UploadFile = File(...), mode: str = Form("fast"), candidates: str = Form("C0,C1,C2"),
                  selection: str = Form("snr_router"), asr_model: str = Form("prisma-v2.5")):
    pcm = to_8k_mono_pcm16(await file.read())
    stats = vad_snr(pcm)
    cands = generate_candidates(pcm, candidates.split(","))
    cands = speech_guard(pcm, cands)
    if selection == "snr_router":
        pick = route_by_snr(stats, cands)
        text = await gnani_prisma(cands[pick], asr_model)           # exactly one call
        transcripts = {pick: text}
    else:
        transcripts = await consensus(cands, asr_model)              # 3–4 calls
        pick = transcripts.pop("_winner")
    return {"selected_candidate": pick, "transcription": transcripts[pick],
            "candidates": {k: {"transcript": transcripts.get(k), "audio_url": save(k, v)} for k, v in cands.items()},
            "analysis": stats}

@app.websocket("/live")
async def live(ws: WebSocket):
    await ws.accept()
    config = await ws.receive_json()
    async with gnani_stream(headers={"x-sample-rate": "8000"}, vad_preset="telephony") as upstream:
        while True:
            frame = await ws.receive_bytes()   # 1,024 bytes every 64 ms
            await upstream.send(frame)
            ...
```
