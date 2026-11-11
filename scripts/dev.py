"""Start the local FastAPI and Vite services, optionally with the local Gnani mock."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def _wait_for_port(port: int, process: subprocess.Popen, name: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited with code {process.returncode} before becoming ready.")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"{name} did not become ready.")


def _wait_for_http(url: str, process: subprocess.Popen, name: str) -> bytes:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited with code {process.returncode} before becoming ready.")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return response.read()
        except (OSError, URLError):
            time.sleep(0.1)
    raise RuntimeError(f"{name} did not return HTTP 200.")


def _stop(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for _, process in reversed(processes):
        if process.poll() is not None:
            continue
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _run(args: argparse.Namespace) -> int:
    env = os.environ.copy()
    backend_url = f"http://127.0.0.1:{args.backend_port}"
    frontend_url = f"http://127.0.0.1:{args.frontend_port}"
    env["VITE_BHARATNOISE_API_URL"] = backend_url
    origins = {
        origin.strip()
        for origin in env.get("BN_CORS_ORIGINS", "").split(",")
        if origin.strip()
    }
    origins.update({frontend_url, f"http://localhost:{args.frontend_port}"})
    env["BN_CORS_ORIGINS"] = ",".join(sorted(origins))

    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        if args.mock:
            env.update({
                "GNANI_API_KEY": "mock-key",
                "GNANI_BASE_URL": f"http://127.0.0.1:{args.mock_port}",
                "GNANI_WS_URL": f"ws://127.0.0.1:{args.mock_port}/stt/v3/stream",
            })
            mock = subprocess.Popen(
                [
                    sys.executable, "-m", "bharatnoise_api.mock_gnani",
                    "--port", str(args.mock_port),
                ],
                cwd=ROOT, env=env,
            )
            processes.append(("Gnani mock", mock))
            _wait_for_port(args.mock_port, mock, "Gnani mock")

        backend = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "bharatnoise_api.server:app",
                "--workers", "1", "--host", "127.0.0.1", "--port", str(args.backend_port),
            ],
            cwd=ROOT, env=env,
        )
        processes.append(("FastAPI", backend))
        health_bytes = _wait_for_http(f"{backend_url}/api/health", backend, "FastAPI")
        health = json.loads(health_bytes)
        if args.mock and health.get("gnani_mode") != "mock":
            raise RuntimeError("FastAPI did not report mock GNANI mode.")

        npm = "npm.cmd" if os.name == "nt" else "npm"
        frontend = subprocess.Popen(
            [
                npm, "run", "dev", "--", "--host", "127.0.0.1",
                "--port", str(args.frontend_port), "--strictPort",
            ],
            cwd=ROOT, env=env,
        )
        processes.append(("Vite", frontend))
        _wait_for_http(frontend_url, frontend, "Vite")

        print(f"FastAPI ready at {backend_url}", flush=True)
        if args.mock:
            print(f"Mock Gnani ready at http://127.0.0.1:{args.mock_port}", flush=True)
            print("Mode: MOCK GNANI; transcripts are placeholders and scores are suppressed.", flush=True)
        else:
            print("Mode: configured from GNANI_API_KEY and GNANI_BASE_URL in the environment or .env.", flush=True)
        print(f"Frontend ready at {frontend_url}", flush=True)
        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    raise RuntimeError(f"{name} exited with code {code}.")
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        _stop(processes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mock", action="store_true", help="Start a local mock Gnani server.")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=8080)
    parser.add_argument("--mock-port", type=int, default=9100)
    args = parser.parse_args()
    try:
        return _run(args)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Development stack failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
