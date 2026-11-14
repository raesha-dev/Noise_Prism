"""Serial, retrying Gnani Prisma REST client with a local content-addressed cache."""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

API_URL = "https://api.vachana.ai/stt/v3"
TTS_API_URL = "https://api.vachana.ai/api/v1/tts/inference"
OFFICIAL_HOST = "api.vachana.ai"


def api_endpoint(base_url: str | None = None) -> str:
    """Resolve the REST endpoint from a host/base URL without relying on cwd."""
    base = (base_url or os.environ.get("GNANI_BASE_URL", "https://api.vachana.ai")).rstrip("/")
    if base.endswith("/stt/v3"):
        return base
    return f"{base}/stt/v3"


def tts_api_endpoint(base_url: str | None = None) -> str:
    """Resolve Gnani Timbre REST URL from the configured API origin."""
    base = (base_url or os.environ.get("GNANI_BASE_URL", "https://api.vachana.ai")).rstrip("/")
    if base.endswith("/stt/v3"):
        base = base[: -len("/stt/v3")]
    return f"{base}/api/v1/tts/inference"


def endpoint_mode(base_url: str | None = None, api_key: str | None = None) -> str:
    """Distinguish absent credentials, official Prisma, and explicitly non-official endpoints."""
    key = api_key if api_key is not None else os.environ.get("GNANI_API_KEY", "")
    if not key:
        return "unconfigured"
    host = (urlparse(base_url or os.environ.get("GNANI_BASE_URL", "https://api.vachana.ai")).hostname or "").lower()
    return "live" if host == OFFICIAL_HOST else "mock"


class FatalAPIError(RuntimeError):
    """A credential, account, or access error that must stop the current run."""


@dataclass
class Transcript:
    ok: bool
    text: str = ""
    model: str = ""
    detected_language_code: str | None = None
    processing_time: float | None = None
    end_to_end_latency: float | None = None
    wall_seconds: float | None = None
    audio_seconds: float | None = None
    truncated: bool = False
    error: str | None = None
    note: str | None = None
    cached: bool = False


class GnaniClient:
    """Call Prisma with required explicit multipart fields; never emit payloads to logs."""

    def __init__(
        self, cache_dir: Path | None = None, api_key: str | None = None,
        timeout_s: float = 90.0, retries: int = 4,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("GNANI_API_KEY", "")
        if not self._api_key:
            raise FatalAPIError("GNANI_API_KEY is not configured.")
        self.api_url = api_endpoint()
        self.tts_api_url = tts_api_endpoint()
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.retries = retries
        self._request_lock = threading.Lock()

    def _cache_path(self, audio_path: Path, language: str, model: str, fmt: str | None, run_id: Any) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(audio_path.read_bytes()).hexdigest()
        identity = "\0".join((digest, model, language, fmt or "auto", str(run_id)))
        return self.cache_dir / f"{hashlib.sha256(identity.encode()).hexdigest()}.json"

    def transcribe(
        self, path: str | Path, language: str, *, model: str = "v2.5",
        fmt: str | None = "verbatim", run_id: Any = 0, extra: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> Transcript:
        """Transcribe one clip; the lock enforces the account's single-active-call limit."""
        audio_path = Path(path)
        prisma_model = model if model.startswith("prisma-") else f"prisma-{model}"
        cache_path = self._cache_path(audio_path, language, prisma_model, fmt, run_id)
        if use_cache and cache_path is not None and cache_path.exists():
            cached_result = json.loads(cache_path.read_text(encoding="utf-8"))
            cached_result["cached"] = True
            return Transcript(**cached_result)
        with self._request_lock:
            started = time.monotonic()
            for attempt in range(self.retries + 1):
                try:
                    with audio_path.open("rb") as audio_file:
                        response = requests.post(
                            self.api_url,
                            headers={"X-API-Key-ID": self._api_key, **(extra or {})},
                            files={"audio_file": (audio_path.name, audio_file, "audio/wav")},
                            data={
                                "language_code": language,
                                "model": prisma_model,
                                **({"format": fmt} if fmt is not None else {}),
                            },
                            timeout=self.timeout_s,
                        )
                except requests.RequestException as exc:
                    if attempt < self.retries:
                        time.sleep(min(8.0, 0.25 * (2**attempt)) * random.uniform(0.75, 1.25))
                        continue
                    return Transcript(ok=False, error=f"NETWORK_ERROR: {type(exc).__name__}",
                                      model=prisma_model, wall_seconds=time.monotonic() - started)
                if response.status_code in {401, 402, 403}:
                    raise FatalAPIError(f"HTTP {response.status_code} GNANI_ACCESS_DENIED")
                retryable = response.status_code == 429 or response.status_code == 504
                if retryable and attempt < self.retries:
                    time.sleep(min(8.0, 0.25 * (2**attempt)) * random.uniform(0.75, 1.25))
                    continue
                if not response.ok:
                    code = "RATE_LIMITED" if response.status_code == 429 else "UPSTREAM_TIMEOUT" if response.status_code == 504 else "HTTP_ERROR"
                    return Transcript(ok=False, error=f"HTTP {response.status_code} {code}",
                                      model=prisma_model, wall_seconds=time.monotonic() - started)
                try:
                    body = response.json()
                except ValueError:
                    return Transcript(ok=False, error="INVALID_RESPONSE: Gnani returned non-JSON data.",
                                      model=prisma_model, wall_seconds=time.monotonic() - started)
                if not body.get("success") or not isinstance(body.get("transcript"), str):
                    return Transcript(ok=False, error="ASR_FAILED: Gnani returned no transcript.",
                                      model=prisma_model, wall_seconds=time.monotonic() - started)
                result = Transcript(
                    ok=True, text=body["transcript"], model=str(body.get("model") or prisma_model),
                    detected_language_code=body.get("detected_language_code"),
                    processing_time=_number(body.get("processing_time")),
                    end_to_end_latency=_number(body.get("end_to_end_latency")),
                    wall_seconds=time.monotonic() - started,
                    truncated=bool(body.get("truncated", False)),
                )
                if use_cache and cache_path is not None:
                    cache_path.write_text(json.dumps(asdict(result), ensure_ascii=False), encoding="utf-8")
                return result
        raise RuntimeError("The Gnani retry loop ended unexpectedly.")

    def synthesize(
        self, text: str, *, language: str = "auto",
    ) -> tuple[bytes, str]:
        """Return full Timbre audio while sharing the serial lock used for Prisma calls."""
        payload = {
            "text": text,
            "voice": "Shivani",
            "model": "timbre-v3.0",
            "language": language,
            "speed": 1.0,
            "audio_config": {
                "sample_rate": 24000,
                "num_channels": 1,
                "sample_width": 2,
                "encoding": "linear_pcm",
                "container": "wav",
            },
        }
        with self._request_lock:
            for attempt in range(self.retries + 1):
                try:
                    response = requests.post(
                        self.tts_api_url,
                        headers={"X-API-Key-ID": self._api_key},
                        json=payload,
                        timeout=self.timeout_s,
                    )
                except requests.RequestException as exc:
                    if attempt < self.retries:
                        time.sleep(min(8.0, 0.25 * (2**attempt)) * random.uniform(0.75, 1.25))
                        continue
                    raise RuntimeError(f"GNANI_TTS_NETWORK_ERROR: {type(exc).__name__}") from exc
                if response.status_code in {401, 402, 403}:
                    raise FatalAPIError(f"HTTP {response.status_code} GNANI_TTS_ACCESS_DENIED")
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.retries:
                        time.sleep(min(8.0, 0.25 * (2**attempt)) * random.uniform(0.75, 1.25))
                        continue
                if not response.ok:
                    raise RuntimeError(f"GNANI_TTS_HTTP_ERROR: HTTP {response.status_code}")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"audio/wav", "audio/x-wav"} or not response.content:
                    raise RuntimeError("GNANI_TTS_INVALID_RESPONSE: Expected non-empty WAV audio.")
                return response.content, "audio/wav"
        raise RuntimeError("The Gnani TTS retry loop ended unexpectedly.")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
