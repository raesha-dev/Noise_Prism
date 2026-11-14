"""Verify configured Gnani REST and streaming endpoints using synthetic silence only."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

import numpy as np
import requests
import soundfile as sf
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

try:
    from ..env import load_environment
    from ..gnani_client import api_endpoint, endpoint_mode
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from env import load_environment
    from gnani_client import api_endpoint, endpoint_mode


class PreflightFailure(RuntimeError):
    """A documented endpoint check did not pass."""


def _validate_rest_body(body: object, model: str, mode: str) -> None:
    """Validate common REST fields without requiring model-dependent optional fields."""
    if not isinstance(body, dict):
        raise PreflightFailure(f"REST {model}: response JSON was not an object.")
    fields = (
        "success",
        "request_id",
        "transcript",
        "model",
        "processing_time",
        "end_to_end_latency",
    )
    missing = [field for field in fields if field not in body]
    if missing:
        raise PreflightFailure(f"REST {model}: missing documented fields {', '.join(missing)}.")
    expected_model = f"mock-{model}" if mode == "mock" else model
    if body["success"] is not True or body["model"] != expected_model:
        raise PreflightFailure(f"REST {model}: unsuccessful response or model echo mismatch.")
    if not isinstance(body["transcript"], str):
        raise PreflightFailure(f"REST {model}: transcript has an invalid type.")
    if "detected_language_code" in body and body["detected_language_code"] is not None:
        if not isinstance(body["detected_language_code"], str):
            raise PreflightFailure(f"REST {model}: detected_language_code has an invalid type.")
    if "truncated" in body and not isinstance(body["truncated"], bool):
        raise PreflightFailure(f"REST {model}: truncated has an invalid type.")


def _silence_wav() -> bytes:
    stream = io.BytesIO()
    sf.write(stream, np.zeros(16_000, dtype=np.float32), 8000, format="WAV", subtype="PCM_16")
    return stream.getvalue()


def _rest_check(api_key: str, mode: str, record_audio: Callable[[float], None]) -> None:
    payload = _silence_wav()
    for model in ("prisma-v2.5", "prisma-v3.0"):
        started = time.monotonic()
        record_audio(2.0)
        try:
            response = requests.post(
                api_endpoint(),
                headers={"X-API-Key-ID": api_key},
                files={"audio_file": (f"preflight-{model}.wav", payload, "audio/wav")},
                data={"language_code": "hi-IN", "model": model, "format": "verbatim"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise PreflightFailure(f"REST {model}: {type(exc).__name__}") from None
        latency = time.monotonic() - started
        if response.status_code in {401, 402, 403, 429}:
            names = {401: "UNAUTHORIZED", 402: "INSUFFICIENT_CREDIT", 403: "FORBIDDEN", 429: "RATE_LIMITED"}
            raise PreflightFailure(f"REST {model}: HTTP {response.status_code} {names[response.status_code]}")
        if response.status_code != 200:
            raise PreflightFailure(f"REST {model}: expected HTTP 200, received HTTP {response.status_code}.")
        try:
            body = response.json()
        except ValueError:
            raise PreflightFailure(f"REST {model}: response was not JSON.") from None
        _validate_rest_body(body, model, mode)
        print(f"REST {model}: HTTP 200, model echo verified, latency {latency:.3f}s")


async def _websocket_check(api_key: str, record_audio: Callable[[float], None]) -> None:
    url = os.environ.get("GNANI_WS_URL", "wss://api.vachana.ai/stt/v3/stream")
    headers = {
        "x-api-key-id": api_key,
        "language_code": "hi-IN",
        "model": "prisma-v3.0",
        "x-sample-rate": "8000",
        "x-vad-threshold": "0.7",
        "x-min-speech-ms": "200",
        "x-min-silence-ms": "500",
    }
    try:
        async with connect(url, additional_headers=headers, open_timeout=5) as websocket:
            first = await asyncio.wait_for(websocket.recv(), timeout=5)
            try:
                connected = json.loads(first)
            except (TypeError, ValueError):
                raise PreflightFailure("WebSocket: first upstream message was not JSON.") from None
            if connected.get("type") != "connected":
                raise PreflightFailure("WebSocket: expected a connected message within five seconds.")

            next_frame = time.monotonic()
            for _ in range(20):
                await websocket.send(bytes(1024))
                record_audio(512 / 8000)
                next_frame += 0.064
                await asyncio.sleep(max(0.0, next_frame - time.monotonic()))
            await websocket.send(json.dumps({"type": "commit"}))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                except ConnectionClosed:
                    break
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    raise PreflightFailure("WebSocket: upstream sent a non-JSON message.") from None
                if message.get("type") == "error":
                    raise PreflightFailure("WebSocket: upstream returned an error during the silence probe.")
    except PreflightFailure:
        raise
    except Exception as exc:
        raise PreflightFailure(f"WebSocket: {type(exc).__name__}") from None
    print("WebSocket: connected, twenty paced PCM16 frames accepted, no error message.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--rest-only", action="store_true", help="Check REST only.")
    group.add_argument("--ws-only", action="store_true", help="Check WebSocket only.")
    args = parser.parse_args()
    load_environment()
    api_key = os.environ.get("GNANI_API_KEY", "")
    if not api_key:
        print("NO KEY: nothing was verified")
        return 3

    mode = endpoint_mode()
    audio_seconds = [0.0]

    def record_audio(seconds: float) -> None:
        audio_seconds[0] += seconds

    try:
        if not args.ws_only:
            _rest_check(api_key, mode, record_audio)
        if not args.rest_only:
            asyncio.run(_websocket_check(api_key, record_audio))
        return 0
    except PreflightFailure as exc:
        message = str(exc).replace(api_key, "[REDACTED]")
        print(f"PREFLIGHT FAILED: {message}", file=sys.stderr)
        return 1
    finally:
        print(f"Total synthetic audio sent: {audio_seconds[0] / 60:.6f} minutes")


if __name__ == "__main__":
    raise SystemExit(main())
