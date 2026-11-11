import csv
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import env as env_loader
from .server import Settings

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent


def test_default_cors_includes_the_vite_development_origin():
    settings = Settings()

    assert "http://127.0.0.1:8080" in settings.cors_origins


def test_env_files_load_in_order_and_process_environment_wins(tmp_path, monkeypatch):
    package = tmp_path / "package"
    repository = tmp_path / "repository"
    package.mkdir()
    repository.mkdir()
    monkeypatch.setattr(env_loader, "PACKAGE_ROOT", package)
    monkeypatch.setattr(env_loader, "REPOSITORY_ROOT", repository)
    monkeypatch.setenv("BN_TEST_VALUE", "real-environment")
    monkeypatch.delenv("BN_DOTENV_ORDER", raising=False)
    monkeypatch.delenv("BN_PACKAGE_ONLY", raising=False)
    monkeypatch.delenv("BN_REPOSITORY_ONLY", raising=False)
    (package / ".env").write_text(
        "BN_TEST_VALUE=package\nBN_DOTENV_ORDER=package\nBN_PACKAGE_ONLY=yes\n", encoding="utf-8"
    )
    (repository / ".env").write_text(
        "BN_TEST_VALUE=repository\nBN_DOTENV_ORDER=repository\nBN_REPOSITORY_ONLY=yes\n", encoding="utf-8"
    )

    env_loader.load_environment()

    assert os.environ["BN_TEST_VALUE"] == "real-environment"
    assert os.environ["BN_DOTENV_ORDER"] == "repository"
    assert os.environ["BN_PACKAGE_ONLY"] == "yes"
    assert os.environ["BN_REPOSITORY_ONLY"] == "yes"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_preflight_returns_three_without_a_key():
    environment = os.environ.copy()
    environment.pop("GNANI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, str(PACKAGE / "scripts" / "preflight_gnani.py")],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 3
    assert result.stdout.strip() == "NO KEY: nothing was verified"


def test_preflight_passes_against_mock_endpoints(tmp_path):
    port = _free_port()
    environment = os.environ.copy()
    environment.update({
        "GNANI_API_KEY": "mock-key",
        "GNANI_BASE_URL": f"http://127.0.0.1:{port}",
        "GNANI_WS_URL": f"ws://127.0.0.1:{port}/stt/v3/stream",
        "BN_DATA_DIR": str(tmp_path),
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "bharatnoise_api.mock_gnani", "--port", str(port)],
        cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                if process.poll() is not None:
                    raise AssertionError("Mock Gnani server exited before becoming ready.")
                time.sleep(0.05)
        else:
            raise AssertionError("Mock Gnani server did not become ready.")

        result = subprocess.run(
            [sys.executable, str(PACKAGE / "scripts" / "preflight_gnani.py")],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "REST prisma-v2.5: HTTP 200" in result.stdout
        assert "REST prisma-v3.0: HTTP 200" in result.stdout
        assert "WebSocket: connected" in result.stdout
        assert "Total synthetic audio sent:" in result.stdout

        audio = tmp_path / "clip.wav"
        sf.write(audio, np.zeros(8000, dtype=np.float32), 8000, format="WAV", subtype="PCM_16")
        manifest = tmp_path / "manifest.csv"
        with manifest.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["id", "audio_path", "language", "reference"])
            writer.writeheader()
            writer.writerow({"id": "synthetic-1", "audio_path": str(audio), "language": "hi-IN", "reference": ""})
        dry_run = subprocess.run(
            [
                sys.executable, str(PACKAGE / "run_baseline.py"),
                "--input", str(manifest), "--output", str(tmp_path / "planned.csv"),
                "--candidates", "C0", "C1", "--models", "v2.5", "v3.0",
                "--runs", "2", "--dry-run",
            ],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        assert dry_run.returncode == 0, dry_run.stdout + dry_run.stderr
        assert "Planned API calls: 8" in dry_run.stdout

        baseline = subprocess.run(
            [
                sys.executable, str(PACKAGE / "run_baseline.py"),
                "--input", str(manifest), "--output", str(tmp_path / "results" / "baseline.csv"),
            ],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=15,
        )
        assert baseline.returncode == 0, baseline.stdout + baseline.stderr
        mock_result = tmp_path / "results_mock" / "MOCK_baseline.csv"
        assert mock_result.is_file()
        with mock_result.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        assert rows[0]["hypothesis"].startswith("[MOCK] ")
        assert not (tmp_path / "results" / "baseline.csv").exists()
    finally:
        process.terminate()
        process.wait(timeout=5)
