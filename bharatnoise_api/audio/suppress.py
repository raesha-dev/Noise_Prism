"""Conservative STFT noise suppression with a configurable observation blend."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "suppress.json"


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Load explicitly flagged suppression placeholders."""
    with path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    config.setdefault("fitted", False)
    return config


def _blend_for_snr(snr_db: float | None, table: list[dict]) -> float:
    value = -100.0 if snr_db is None else float(snr_db)
    for row in sorted(table, key=lambda item: float(item["min_snr_db"]), reverse=True):
        if value >= float(row["min_snr_db"]):
            return float(row["lambda"])
    return float(table[-1]["lambda"]) if table else 0.0


def suppress(x: np.ndarray, speech_mask: np.ndarray, snr_db: float | None, config: dict | None = None) -> tuple[np.ndarray, dict[str, float]]:
    """Denoise in the STFT domain, blend back the observation, and peak-limit safely."""
    cfg = config or load_config()
    source = np.asarray(x, dtype=np.float32)
    if source.size < 256:
        return source.copy(), {"gain_applied_db": 0.0, "observation_lambda": 1.0}
    _, _, z = signal.stft(source, fs=8000, window="hann", nperseg=256, noverlap=192,
                          boundary="zeros", padded=True)
    power = np.abs(z) ** 2
    hop = 64
    frame_indices = np.minimum(np.arange(power.shape[1]) * hop // 160, max(0, len(speech_mask) - 1))
    non_speech = ~speech_mask[frame_indices] if len(speech_mask) else np.zeros(power.shape[1], dtype=bool)
    if non_speech.sum() >= 2:
        noise_power = np.median(power[:, non_speech], axis=1, keepdims=True)
    else:
        fallback_count = max(1, min(power.shape[1], int(np.ceil(0.3 * 8000 / hop))))
        first_noise = power[:, :fallback_count]
        if first_noise.shape[1] >= 2:
            noise_power = np.median(first_noise, axis=1, keepdims=True)
        else:
            noise_power = np.min(power, axis=1, keepdims=True)
    floor = float(cfg.get("wiener_gain_floor", 0.18))
    noise_power = np.maximum(noise_power, np.max(power, axis=1, keepdims=True) * 1e-8)
    gain = np.maximum(floor, power / (power + noise_power + 1e-12))
    smoothing = float(cfg.get("temporal_smoothing", 0.75))
    for index in range(1, gain.shape[1]):
        gain[:, index] = smoothing * gain[:, index - 1] + (1 - smoothing) * gain[:, index]
    _, enhanced = signal.istft(z * gain, fs=8000, window="hann", nperseg=256, noverlap=192,
                               input_onesided=True, boundary=True)
    enhanced = enhanced[:len(source)]
    if len(enhanced) < len(source):
        enhanced = np.pad(enhanced, (0, len(source) - len(enhanced)))
    blend = _blend_for_snr(snr_db, cfg.get("observation_blend", []))
    output = enhanced + blend * source
    peak = float(np.max(np.abs(output))) if output.size else 0.0
    limiter = min(1.0, 0.99 / max(peak, 1e-12))
    output = (output * limiter).astype(np.float32)
    in_rms = float(np.sqrt(np.mean(source.astype(np.float64) ** 2))) if source.size else 0.0
    out_rms = float(np.sqrt(np.mean(output.astype(np.float64) ** 2))) if output.size else 0.0
    gain_db = 20 * np.log10(max(out_rms, 1e-12) / max(in_rms, 1e-12))
    return output, {"gain_applied_db": float(gain_db), "observation_lambda": blend}
