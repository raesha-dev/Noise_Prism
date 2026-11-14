"""Compare documented WebSocket VAD settings for one WAV; transcript text is never logged."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import soundfile as sf
from scipy import signal

try:
    import websockets
except ImportError as exc:
    raise SystemExit("stream_clip requires websockets>=13.") from exc

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 8000
FRAME_BYTES = 1024
FRAME_SECONDS = 0.064
MAX_SESSION_SECONDS = 15 * 60


def load_pcm_frames(path: Path) -> tuple[list[bytes], float]:
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if samples.size == 0:
        raise ValueError("Audio file is empty.")
    if not np.all(np.isfinite(samples)):
        raise ValueError("Audio contains non-finite samples.")
    mono = samples.mean(axis=1).astype(np.float32)
    if sample_rate != SAMPLE_RATE:
        from math import gcd

        divisor = gcd(int(sample_rate), SAMPLE_RATE)
        mono = signal.resample_poly(mono, SAMPLE_RATE // divisor, int(sample_rate) // divisor).astype(np.float32)
    duration = len(mono) / SAMPLE_RATE
    if duration > MAX_SESSION_SECONDS:
        raise ValueError("Audio exceeds the 15-minute streaming session limit.")
    pcm = np.round(np.clip(mono, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    silence = bytes(SAMPLE_RATE * 2)
    payload = pcm + silence
    payload += bytes((-len(payload)) % FRAME_BYTES)
    frames = [payload[index:index + FRAME_BYTES] for index in range(0, len(payload), FRAME_BYTES)]
    return frames, duration


def upgrade_headers(api_key: str, language: str, model: str, args: argparse.Namespace) -> dict[str, str]:
    if model == "v2.5" and language == "indic-auto":
        raise ValueError("indic-auto is only documented for Prisma v3.0.")
    return {
        "x-api-key-id": api_key,
        "language_code": language,
        "model": f"prisma-{model}",
        "x-sample-rate": "8000",
        "x-vad-threshold": str(args.vad_threshold),
        "x-min-speech-ms": str(args.min_speech_ms),
        "x-min-silence-ms": str(args.min_silence_ms),
        "x-min-segment-ms": str(args.min_segment_ms),
        "x-max-speech-s": str(args.max_speech_s),
    }


async def stream(args: argparse.Namespace) -> int:
    api_key = os.environ.get("GNANI_API_KEY", "")
    if not api_key:
        raise ValueError("GNANI_API_KEY is not configured.")
    frames, duration = load_pcm_frames(args.audio)
    uri = os.environ.get("GNANI_WS_URL", "wss://api.vachana.ai/stt/v3/stream")
    if urlparse(uri).hostname != "api.vachana.ai" and not args.allow_nonofficial:
        raise ValueError("Non-official WebSocket endpoints require --allow-nonofficial.")
    headers = upgrade_headers(api_key, args.language, args.model, args)
    counts: dict[str, int] = {}
    reported_latencies: list[float] = []
    connection = websockets.connect(uri, additional_headers=headers, max_size=1_000_000)
    async with connection as websocket:
        started = time.monotonic()
        for index, frame in enumerate(frames):
            target = started + index * FRAME_SECONDS
            delay = target - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            await websocket.send(frame)
        await websocket.send(json.dumps({"type": "commit"}))
        deadline = time.monotonic() + args.final_timeout
        while time.monotonic() < deadline:
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=max(0.1, deadline - time.monotonic()))
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                break
            if not isinstance(message, str):
                continue
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                counts["invalid_json"] = counts.get("invalid_json", 0) + 1
                continue
            kind = str(event.get("type", "unknown"))
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "transcript":
                try:
                    reported_latencies.append(float(event["latency"]))
                except (KeyError, TypeError, ValueError):
                    pass
    print(json.dumps({
        "streaming_only": True,
        "transcript_text_logged": False,
        "duration_seconds": round(duration, 3),
        "frames_sent": len(frames),
        "frame_bytes": FRAME_BYTES,
        "frame_interval_ms": FRAME_SECONDS * 1000,
        "events": counts,
        "transcript_events": counts.get("transcript", 0),
        "latency_values_reported": reported_latencies,
        "vad": {
            "threshold": args.vad_threshold,
            "min_speech_ms": args.min_speech_ms,
            "min_silence_ms": args.min_silence_ms,
            "min_segment_ms": args.min_segment_ms,
            "max_speech_s": args.max_speech_s,
        },
    }, indent=2))
    return 1 if counts.get("error", 0) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--model", choices=("v2.5", "v3.0"), default="v3.0")
    parser.add_argument("--language", default="hi-IN")
    parser.add_argument("--vad-threshold", type=float, default=0.7)
    parser.add_argument("--min-speech-ms", type=int, default=200)
    parser.add_argument("--min-silence-ms", type=int, default=500)
    parser.add_argument("--min-segment-ms", type=int, default=0)
    parser.add_argument("--max-speech-s", type=int, default=15)
    parser.add_argument("--final-timeout", type=float, default=15)
    parser.add_argument("--allow-nonofficial", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.vad_threshold <= 1 or min(
        args.min_speech_ms, args.min_silence_ms, args.min_segment_ms, args.max_speech_s
    ) < 0:
        parser.error("VAD thresholds and durations must be non-negative and the threshold at most one.")
    try:
        return asyncio.run(stream(args))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"STREAM ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
