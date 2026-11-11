"""Exercise the local mock, FastAPI routes, queued modes, and live WebSocket proxy."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import requests
import soundfile as sf
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Mock Gnani exited before becoming ready (code {process.returncode}).")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("Mock Gnani did not become ready.")


def _synthetic_wav() -> bytes:
    sample_rate = 8000
    time_axis = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
    samples = (0.25 * np.sin(2 * np.pi * 440 * time_axis)).astype(np.float32)
    with tempfile.SpooledTemporaryFile() as stream:
        sf.write(stream, samples, sample_rate, format="WAV", subtype="PCM_16")
        stream.seek(0)
        return stream.read()


def _wait_for_backend(url: str, process: subprocess.Popen, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Backend exited before becoming ready (code {process.returncode}).")
        try:
            response = requests.get(f"{url}/api/health", timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            time.sleep(0.1)
    raise RuntimeError("Backend did not become ready.")


def _assert_status(response: requests.Response, expected: int, label: str) -> Any:
    if response.status_code != expected:
        try:
            error = response.json().get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
        except ValueError:
            code = None
        suffix = f" ({code})" if isinstance(code, str) else ""
        raise RuntimeError(f"{label} returned HTTP {response.status_code}{suffix}, expected {expected}.")
    try:
        return response.json()
    except ValueError:
        raise RuntimeError(f"{label} did not return JSON.") from None


def _poll_job(base_url: str, job_id: str) -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = requests.get(f"{base_url}/api/jobs/{job_id}", timeout=10)
        body = _assert_status(response, 200, "Job status")
        if body.get("status") in {"done", "error"}:
            if body["status"] != "done" or not isinstance(body.get("result"), dict):
                error = body.get("error")
                code = error.get("code") if isinstance(error, dict) else "unknown"
                raise RuntimeError(f"Job failed with {code}.")
            return body["result"]
        time.sleep(0.1)
    raise RuntimeError("Job did not finish before the E2E timeout.")


async def _check_live(base_url: str, path: str) -> None:
    url = f"{base_url.replace('http://', 'ws://')}/{path}?language=hi-IN&model=v2.5&variant=C0&sample_rate=8000"
    frame = np.full(512, 8000, dtype="<i2").tobytes()
    connected = False
    transcript = False
    async with connect(url, open_timeout=10, close_timeout=5) as websocket:
        for _ in range(47):
            await websocket.send(frame)
            await asyncio.sleep(0.064)
        await websocket.send(json.dumps({"type": "stop"}))
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=3)
            except TimeoutError:
                continue
            payload = json.loads(message)
            if payload.get("type") == "error":
                raise RuntimeError(f"{path} returned a live error.")
            if payload.get("type") == "connected":
                connected = True
                config = payload.get("config", {})
                if not str(config.get("model", "")).startswith("mock-"):
                    raise RuntimeError(f"{path} did not identify the upstream mock model.")
            if payload.get("type") == "transcript" and str(payload.get("text", "")).startswith("[MOCK]"):
                transcript = True
                break
    if not connected:
        raise RuntimeError(f"{path} did not relay the connected message.")
    if not transcript:
        raise RuntimeError(f"{path} did not relay a mock transcript.")


def _run() -> None:
    backend_port, mock_port = _free_port(), _free_port()
    while mock_port == backend_port:
        mock_port = _free_port()
    with tempfile.TemporaryDirectory(prefix="bharatnoise-e2e-") as data_dir:
        env = os.environ.copy()
        env.update({
            "GNANI_API_KEY": "mock-key",
            "GNANI_BASE_URL": f"http://127.0.0.1:{mock_port}",
            "GNANI_WS_URL": f"ws://127.0.0.1:{mock_port}/stt/v3/stream",
            "BN_DATA_DIR": data_dir,
            "BN_DAILY_CALL_CAP": "300",
            "BN_LIVE_STOP_GRACE_S": "1",
        })
        mock = subprocess.Popen(
            [sys.executable, "-m", "bharatnoise_api.mock_gnani", "--port", str(mock_port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        backend = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "bharatnoise_api.server:app",
                "--workers", "1", "--host", "127.0.0.1", "--port", str(backend_port),
            ],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        base_url = f"http://127.0.0.1:{backend_port}"
        try:
            _wait_for_port(mock_port, mock)
            _wait_for_backend(base_url, backend)
            health = _assert_status(requests.get(f"{base_url}/api/health", timeout=5), 200, "Health")
            if health.get("gnani_mode") != "mock":
                raise RuntimeError("Backend did not report mock GNANI mode.")

            wav = _synthetic_wav()
            upload = requests.post(
                f"{base_url}/api/clips",
                files={"file": ("synthetic-tone.wav", wav, "audio/wav")},
                data={"language": "hi-IN"},
                timeout=20,
            )
            clip = _assert_status(upload, 201, "Clip upload")
            clip_id = clip.get("clip_id")
            if not isinstance(clip_id, str):
                raise RuntimeError("Clip upload did not return a clip identifier.")

            legacy = requests.post(
                f"{base_url}/process",
                files={"file": ("synthetic-tone.wav", wav, "audio/wav")},
                data={
                    "mode": "fast", "sample_rate": "8000", "channels": "1",
                    "sample_format": "pcm_s16le", "asr_provider": "gnani_prisma",
                    "asr_model": "prisma-v2.5", "candidates": "C0,C1,C2",
                    "selection": "snr_router", "compare_models": "false",
                },
                timeout=90,
            )
            legacy_body = _assert_status(legacy, 200, "Legacy process")
            if legacy_body.get("gnani_mode") != "mock" or not str(legacy_body.get("transcription", "")).startswith("[MOCK]"):
                raise RuntimeError("Legacy process did not return unmistakable mock output.")

            for mode in ("raw", "fast", "compare_all", "accuracy"):
                queued = requests.post(
                    f"{base_url}/api/jobs",
                    json={
                        "clip_id": clip_id, "mode": mode, "language": "hi-IN",
                        "model": "v2.5", "candidate_ids": ["C0", "C1", "C2"],
                    },
                    timeout=10,
                )
                job = _assert_status(queued, 202, f"{mode} job creation")
                result = _poll_job(base_url, str(job["job_id"]))
                if result.get("gnani_mode") != "mock" or result.get("score_suppressed_reason") != "mock":
                    raise RuntimeError(f"{mode} job did not preserve mock provenance.")
                if not all(
                    str(item.get("text", "")).startswith("[MOCK]")
                    for item in result.get("hypotheses", []) if item.get("ok")
                ):
                    raise RuntimeError(f"{mode} job returned an unmarked mock hypothesis.")

            score = requests.post(
                f"{base_url}/api/score",
                json={"reference": "", "hypothesis": ""},
                timeout=10,
            )
            score_body = _assert_status(score, 200, "Score")
            if score_body.get("score") is not None:
                raise RuntimeError("Empty-reference scoring unexpectedly returned a score.")

            results = _assert_status(requests.get(f"{base_url}/api/results", timeout=5), 200, "Results")
            if results != []:
                raise RuntimeError("Mock runs unexpectedly wrote to the served results directory.")
            evaluation = _assert_status(requests.get(f"{base_url}/evaluation", timeout=5), 200, "Evaluation")
            if evaluation.get("available") is not False:
                raise RuntimeError("Evaluation did not return its explicit empty state.")

            asyncio.run(_check_live(base_url, "ws/live"))
            asyncio.run(_check_live(base_url, "live"))
        finally:
            for process in (backend, mock):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    print("E2E PASS: mock mode, upload, legacy process, four job modes, score, empty results, and live routes.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        _run()
    except (OSError, requests.RequestException, RuntimeError, asyncio.TimeoutError) as exc:
        print(f"E2E FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
