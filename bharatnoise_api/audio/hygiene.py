"""Telephone-band cleanup used by candidate C1."""
from __future__ import annotations

import numpy as np
from scipy import signal

SAMPLE_RATE = 8000
_BANDPASS = signal.butter(4, [300, 3400], btype="bandpass", fs=SAMPLE_RATE, output="sos")


def hygiene(x: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """Remove DC, band-limit to telephone speech, then target -20 dBFS without clipping."""
    source = np.asarray(x, dtype=np.float32)
    centered = source - float(np.mean(source))
    filtered = signal.sosfiltfilt(_BANDPASS, centered).astype(np.float32)
    rms = float(np.sqrt(np.mean(filtered.astype(np.float64) ** 2))) if filtered.size else 0.0
    peak = float(np.max(np.abs(filtered))) if filtered.size else 0.0
    gain = min(0.1 / max(rms, 1e-9), 0.99 / max(peak, 1e-9))
    result = np.clip(filtered * gain, -0.99, 0.99).astype(np.float32)
    return result, {"gain_applied_db": float(20 * np.log10(max(gain, 1e-12)))}
