"""Speech activity detection with an optional Silero implementation and energy fallback."""
from __future__ import annotations

import threading

import numpy as np
from scipy import signal

_LOCK = threading.Lock()
_MODEL = None
_GET_TIMESTAMPS = None
_ERROR: str | None = None


def _silero_model():
    global _MODEL, _GET_TIMESTAMPS, _ERROR
    with _LOCK:
        if _MODEL is not None or _ERROR is not None:
            return _MODEL, _GET_TIMESTAMPS, _ERROR
        try:
            from silero_vad import get_speech_timestamps, load_silero_vad

            _MODEL = load_silero_vad()
            _GET_TIMESTAMPS = get_speech_timestamps
        except Exception as exc:
            _ERROR = f"{type(exc).__name__}: {exc}"
        return _MODEL, _GET_TIMESTAMPS, _ERROR


def silero_availability() -> tuple[bool, str | None]:
    """Load Silero lazily and return its availability without failing application startup."""
    model, _, error = _silero_model()
    return model is not None, error


def adaptive_energy(x: np.ndarray, frame_size: int = 160) -> tuple[np.ndarray, np.ndarray]:
    """Return a frame-level speech mask and power using an adaptive noise-relative threshold."""
    usable = len(x) - len(x) % frame_size
    if usable == 0:
        return np.zeros(0, dtype=bool), np.zeros(0, dtype=np.float64)
    frames = x[:usable].reshape(-1, frame_size)
    power = np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12
    db = 10 * np.log10(power)
    floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 95))
    mask = db > floor + max(6.0, 0.35 * (peak - floor))
    return mask, power


def speech_mask(x: np.ndarray, sample_rate: int = 8000, frame_size: int = 160) -> tuple[np.ndarray, str, str | None]:
    """Prefer Silero; retry at 16 kHz when 8 kHz is rejected, then use energy VAD."""
    model, timestamps, error = _silero_model()
    if model is not None and timestamps is not None:
        try:
            import torch

            samples = x
            rate = sample_rate
            try:
                regions = timestamps(torch.from_numpy(samples.copy()), model, sampling_rate=rate)
            except Exception:
                rate = 16000
                samples = signal.resample_poly(x, 2, 1).astype(np.float32)
                regions = timestamps(torch.from_numpy(samples.copy()), model, sampling_rate=rate)
            count = len(x) // frame_size
            mask = np.zeros(count, dtype=bool)
            for region in regions:
                start = max(0, int(region["start"]) / rate)
                end = min(len(x) / sample_rate, int(region["end"]) / rate)
                first = max(0, int(start * sample_rate / frame_size))
                last = min(count, int(np.ceil(end * sample_rate / frame_size)))
                mask[first:last] = True
            return mask, "silero", None
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    mask, _ = adaptive_energy(x, frame_size)
    return mask, "adaptive-energy", error
