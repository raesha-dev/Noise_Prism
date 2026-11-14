"""Local, unmistakable Gnani REST and streaming protocol mock for plumbing tests."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import time
import uuid

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

LANGUAGES = {
    "hi-IN", "kn-IN", "en-IN", "ta-IN", "te-IN", "bn-IN",
    "gu-IN", "ml-IN", "mr-IN", "pa-IN",
}
MODELS = {"prisma-v2.5", "prisma-v3.0"}
FRAME_BYTES = 1024
FRAME_SECONDS = 0.064
ENERGY_GATE = 0.008


def create_app(processing_delay_s: float = 0.0) -> FastAPI:
    """Create the local mock app without creating files or logging request content."""
    app = FastAPI(title="BharatNoise Mock Gnani", version="1.0")
    active_request = asyncio.Lock()

    @app.middleware("http")
    async def identify_mock_response(request, call_next):
        response = await call_next(request)
        response.headers["X-Mock-Gnani"] = "1"
        return response

    @app.post("/stt/v3")
    async def transcribe(
        request: Request,
        audio_file: UploadFile = File(...),
        language_code: str | None = Form(None),
        model: str = Form(...),
        format: str | None = Form(None),
    ):
        if not request.headers.get("x-api-key-id"):
            return _error(401, "AUTHENTICATION_REQUIRED", "X-API-Key-ID is required.")
        if not audio_file or not audio_file.filename:
            return _error(400, "INVALID_AUDIO", "audio_file is required.")
        if model not in MODELS:
            return _error(400, "INVALID_MODEL", "model must be prisma-v2.5 or prisma-v3.0.")
        if format not in {None, "verbatim", "transcribe", "codemix", "transliterate"}:
            return _error(400, "INVALID_FORMAT", "format must be verbatim, transcribe, codemix, or transliterate.")
        if format in {"codemix", "transliterate"} and model != "prisma-v3.0":
            return _error(400, "INVALID_FORMAT", f"format={format} requires prisma-v3.0.")
        if language_code is None and format == "transliterate" and model == "prisma-v3.0":
            language_code = "indic-auto"
        if language_code not in LANGUAGES and not (language_code == "indic-auto" and model == "prisma-v3.0"):
            return _error(422, "INVALID_LANGUAGE", "language_code is not supported for this model.")
        if active_request.locked():
            return _error(429, "RATE_LIMITED", "One request is already active.")

        await active_request.acquire()
        try:
            started = time.monotonic()
            payload = await audio_file.read()
            try:
                audio, sample_rate = sf.read(io.BytesIO(payload), dtype="float32", always_2d=True)
            except (RuntimeError, ValueError, sf.LibsndfileError):
                return _error(400, "INVALID_AUDIO", "audio_file could not be decoded.")
            if sample_rate <= 0 or not len(audio):
                return _error(400, "INVALID_AUDIO", "audio_file could not be decoded.")
            duration = len(audio) / float(sample_rate)
            if duration > 30:
                return _error(400, "AUDIO_TOO_LONG", "audio_file must be 30 seconds or shorter.")
            if processing_delay_s > 0:
                await asyncio.sleep(processing_delay_s)
            model_echo = f"mock-{model}"
            fingerprint = hashlib.sha256(payload + model.encode("utf-8")).hexdigest()[:8]
            elapsed = time.monotonic() - started
            return {
                "success": True,
                "request_id": f"mock-{uuid.uuid4().hex}",
                "transcript": f"[MOCK] {fingerprint} {duration:.3f}s",
                "model": model_echo,
                "detected_language_code": None if language_code == "indic-auto" else language_code,
                "processing_time": elapsed,
                "end_to_end_latency": time.monotonic() - started,
                "truncated": False,
            }
        finally:
            active_request.release()

    @app.websocket("/stt/v3/stream")
    async def stream(websocket: WebSocket):
        await websocket.accept(headers=[(b"x-mock-gnani", b"1")])
        headers = websocket.headers
        key = headers.get("x-api-key-id")
        language = headers.get("language_code")
        sample_rate = headers.get("x-sample-rate")
        model = headers.get("model", "prisma-v2.5")
        if not key:
            await _ws_error(websocket, "X-API-Key-ID is required.")
            return
        if not language or (language not in LANGUAGES and not (language == "indic-auto" and model == "prisma-v3.0")):
            await _ws_error(websocket, "language_code is required and must be supported.")
            return
        if sample_rate != "8000":
            await _ws_error(websocket, "x-sample-rate must be 8000.")
            return
        if model not in MODELS:
            await _ws_error(websocket, "model must be prisma-v2.5 or prisma-v3.0.")
            return

        min_silence_ms = _header_int(headers.get("x-min-silence-ms"), 500)
        silence_frames_required = max(1, int(np.ceil(min_silence_ms / (FRAME_SECONDS * 1000))))
        await websocket.send_json({
            "type": "connected",
            "config": {"language_code": language, "model": f"mock-{model}", "sample_rate": 8000, "frame_bytes": FRAME_BYTES},
        })
        segment_index = 0
        active_speech = False
        quiet_frames = 0
        last_frame_at: float | None = None

        async def finish_segment() -> None:
            nonlocal segment_index, active_speech, quiet_frames
            if active_speech:
                segment_index += 1
                await websocket.send_json({"type": "processing"})
                await websocket.send_json({"type": "transcript", "text": f"[MOCK] segment {segment_index}", "segment_index": segment_index})
            active_speech = False
            quiet_frames = 0

        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                frame = message.get("bytes")
                if frame is not None:
                    now = time.monotonic()
                    if len(frame) != FRAME_BYTES:
                        await _ws_error(websocket, "Binary audio frames must be exactly 1,024 bytes.")
                        await websocket.close(code=1008)
                        return
                    if last_frame_at is not None and now - last_frame_at < 0.01:
                        await _ws_error(websocket, "Frame cadence was violated; send frames at 64 ms intervals.")
                        await websocket.close(code=1008)
                        return
                    last_frame_at = now
                    samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
                    is_speech = float(np.sqrt(np.mean(np.square(samples)))) >= ENERGY_GATE
                    if is_speech:
                        if not active_speech:
                            active_speech = True
                            await websocket.send_json({"type": "speech_start"})
                        quiet_frames = 0
                    elif active_speech:
                        quiet_frames += 1
                        if quiet_frames >= silence_frames_required:
                            await finish_segment()
                    continue
                text = message.get("text")
                if text is not None:
                    if text == '{"type":"commit"}':
                        await finish_segment()
                        continue
                    try:
                        command = json.loads(text)
                    except ValueError:
                        await _ws_error(websocket, "Expected a JSON commit control frame.")
                        await websocket.close(code=1008)
                        return
                    if isinstance(command, dict) and command.get("type") == "commit":
                        await finish_segment()
                    else:
                        await _ws_error(websocket, "Only the commit control frame is supported.")
                        await websocket.close(code=1008)
                        return
        except WebSocketDisconnect:
            return

    return app


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "error_code": code, "message": message})


async def _ws_error(websocket: WebSocket, message: str) -> None:
    await websocket.send_json({"type": "error", "message": message})


def _header_int(value: str | None, default: int) -> int:
    try:
        return max(0, int(value)) if value is not None else default
    except ValueError:
        return default


def main() -> None:
    """Run the local mock protocol server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, workers=1)


app = create_app()


if __name__ == "__main__":
    main()
