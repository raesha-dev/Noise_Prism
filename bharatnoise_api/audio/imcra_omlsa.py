"""Experimental simplified spectral noise tracking, gain, and observation addition.

The experimental two-pass minimum tracker and speech-presence-weighted
log-spectral amplitude gain are research-inspired, but have not been verified
as faithful implementations of the cited algorithms. Parameters are explicit
because frame, threshold, and smoothing choices require evaluation on
development data.

References:
    I. Cohen and B. Berdugo, Speech Enhancement for Non-Stationary Noise
    Environments, Signal Processing 81(11), 2001,
    https://doi.org/10.1016/S0165-1684(01)00128-1
    I. Cohen, Noise Spectrum Estimation in Adverse Environments: Improved
    Minima Controlled Recursive Averaging, IEEE TASLP 11(5), 2003,
    https://doi.org/10.1109/TSA.2003.811544
    T. Ochiai et al., How Bad Are Artifacts? Analyzing the Impact of Speech
    Enhancement Errors on ASR, Interspeech 2022,
    https://arxiv.org/abs/2201.06685
"""
from __future__ import annotations

from collections import deque
from math import ceil
from typing import Iterable

import numpy as np
from scipy import signal
from scipy.ndimage import convolve1d
from scipy.special import exp1

SAMPLE_RATE = 8000
WINDOW = 256
HOP = 64
_FREQUENCY_SMOOTHING = np.asarray([0.25, 0.5, 0.25], dtype=np.float64)

DEFAULT_CONFIG: dict[str, float | int] = {
    "window": WINDOW,
    "hop": HOP,
    "smoothing_alpha": 0.8,
    "noise_alpha": 0.98,
    "minimum_window_frames": 80,
    "minimum_update_frames": 8,
    "stage1_threshold": 2.0,
    "stage2_threshold": 1.5,
    "presence_transition": 0.25,
    "decision_direct_alpha": 0.98,
    "gain_floor": 0.05,
    "gain_smoothing": 0.2,
    "speech_level_target_dbfs": -22.0,
    "speech_gain_max": 8.0,
    "noise_gate_gain": 0.25,
    "oa_lambda": 0.1,
    "oa_sweep": [0.0, 0.05, 0.1, 0.2, 0.3, 0.4],
    "peak_limit": 0.99,
}


def _validate_config(config: dict) -> dict:
    merged = {**DEFAULT_CONFIG, **config}
    if int(merged["window"]) != WINDOW or int(merged["hop"]) != HOP:
        raise ValueError(f"IMCRA-OM-LSA uses a {WINDOW}-sample window and {HOP}-sample hop.")
    if not 0 <= float(merged["smoothing_alpha"]) < 1:
        raise ValueError("smoothing_alpha must be in [0, 1).")
    if not 0 <= float(merged["noise_alpha"]) < 1:
        raise ValueError("noise_alpha must be in [0, 1).")
    if not 0 < float(merged["gain_floor"]) <= 1:
        raise ValueError("gain_floor must be in (0, 1].")
    if not 0 <= float(merged["gain_smoothing"]) < 1:
        raise ValueError("gain_smoothing must be in [0, 1).")
    if not -60 <= float(merged["speech_level_target_dbfs"]) <= 0:
        raise ValueError("speech_level_target_dbfs must be between -60 and 0.")
    if float(merged["speech_gain_max"]) < 1:
        raise ValueError("speech_gain_max must be at least 1.")
    if not 0 <= float(merged["noise_gate_gain"]) <= 1:
        raise ValueError("noise_gate_gain must be in [0, 1].")
    if not 0 <= float(merged["oa_lambda"]) <= 0.4:
        raise ValueError("oa_lambda must be between 0 and 0.4.")
    if not 0 < float(merged["peak_limit"]) <= 1:
        raise ValueError("peak_limit must be in (0, 1].")
    if int(merged["minimum_window_frames"]) < 1 or int(merged["minimum_update_frames"]) < 1:
        raise ValueError("IMCRA minimum-tracking frame counts must be positive.")
    return merged


def _smooth_time(power: np.ndarray, alpha: float) -> np.ndarray:
    smoothed = np.empty_like(power, dtype=np.float64)
    if power.shape[1] == 0:
        return smoothed
    smoothed[:, 0] = power[:, 0]
    for frame in range(1, power.shape[1]):
        smoothed[:, frame] = alpha * smoothed[:, frame - 1] + (1 - alpha) * power[:, frame]
    return smoothed


def _minimum_track(smoothed: np.ndarray, window_frames: int, update_frames: int) -> np.ndarray:
    """Track running minima in periodically updated, finite-duration subwindows."""
    bins, frames = smoothed.shape
    result = np.empty_like(smoothed)
    history: deque[np.ndarray] = deque(maxlen=max(1, ceil(window_frames / update_frames)))
    block_min = np.full(bins, np.inf, dtype=np.float64)
    current_min = np.full(bins, np.inf, dtype=np.float64)
    for frame in range(frames):
        block_min = np.minimum(block_min, smoothed[:, frame])
        if (frame + 1) % update_frames == 0 or frame + 1 == frames:
            history.append(block_min.copy())
            current_min = np.min(np.stack(tuple(history), axis=0), axis=0)
            block_min.fill(np.inf)
        result[:, frame] = np.minimum(current_min, smoothed[:, frame])
    return np.maximum(result, np.finfo(np.float64).tiny)


def _speech_probability(smoothed: np.ndarray, minimum: np.ndarray, threshold: float, transition: float) -> np.ndarray:
    ratio = smoothed / np.maximum(minimum, np.finfo(np.float64).tiny)
    raw = np.clip((ratio - threshold + transition) / (2 * transition), 0.0, 1.0)
    return np.clip(convolve1d(raw, _FREQUENCY_SMOOTHING, axis=0, mode="nearest"), 0.0, 1.0)


def _imcra_noise_and_presence(power: np.ndarray, config: dict) -> tuple[np.ndarray, np.ndarray]:
    """Return frequency-dependent noise PSD and estimated speech-presence probability.

    The first pass finds likely speech using a smoothed periodogram relative to
    tracked local minima. A second, frequency-smoothed pass refines that
    probability; the noise PSD only adapts in proportion to estimated speech
    absence. This avoids using a frame-level VAD as ground truth.
    """
    alpha = float(config["smoothing_alpha"])
    window = int(config["minimum_window_frames"])
    update = int(config["minimum_update_frames"])
    transition = float(config["presence_transition"])

    first_smooth = _smooth_time(power, alpha)
    first_min = _minimum_track(first_smooth, window, update)
    first_presence = _speech_probability(
        first_smooth, first_min, float(config["stage1_threshold"]), transition
    )

    second_input = convolve1d(power, _FREQUENCY_SMOOTHING, axis=0, mode="nearest")
    second_smooth = _smooth_time(second_input, alpha)
    second_min = _minimum_track(second_smooth, window, update)
    second_presence = _speech_probability(
        second_smooth, second_min, float(config["stage2_threshold"]), transition
    )
    presence = np.maximum(first_presence, second_presence)

    floor = max(float(np.max(power)) * 1e-12, np.finfo(np.float64).tiny)
    noise = np.empty_like(power, dtype=np.float64)
    noise[:, 0] = np.maximum(np.minimum(first_smooth[:, 0], second_min[:, 0]), floor)
    noise_alpha = float(config["noise_alpha"])
    for frame in range(power.shape[1]):
        if frame:
            absence = 1.0 - presence[:, frame]
            weight = (1 - noise_alpha) * absence
            noise[:, frame] = (1 - weight) * noise[:, frame - 1] + weight * power[:, frame]
        noise[:, frame] = np.maximum(noise[:, frame], floor)
    return noise, presence


def _omlsa_gain(power: np.ndarray, noise: np.ndarray, presence: np.ndarray, config: dict) -> np.ndarray:
    """Compute speech-presence-weighted Ephraim-Malah log-spectral amplitude gain."""
    floor = max(float(np.max(power)) * 1e-12, np.finfo(np.float64).tiny)
    noise = np.maximum(noise, floor)
    gamma = np.maximum(power / noise, floor)
    xi = np.maximum(gamma - 1.0, 0.0)
    alpha = float(config["decision_direct_alpha"])
    if not 0 <= alpha < 1:
        raise ValueError("decision_direct_alpha must be in [0, 1).")
    speech_prior = np.clip(presence, 1e-4, 1 - 1e-4)
    gain_floor = float(config["gain_floor"])
    gain = np.empty_like(xi)
    for frame in range(xi.shape[1]):
        if frame:
            xi[:, frame] = (
                alpha * gain[:, frame - 1] ** 2 * gamma[:, frame - 1]
                + (1 - alpha) * xi[:, frame]
            )
        prior_snr = np.maximum(xi[:, frame], floor)
        v = gamma[:, frame] * prior_snr / (1 + prior_snr)
        gain_h1 = prior_snr / (1 + prior_snr) * np.exp(0.5 * exp1(np.maximum(v, floor)))
        log_likelihood = (
            np.log(speech_prior[:, frame])
            - np.log1p(-speech_prior[:, frame])
            + np.log1p(prior_snr)
            - v
        )
        posterior_presence = 1.0 / (1.0 + np.exp(-np.clip(log_likelihood, -60, 60)))
        log_gain = posterior_presence * np.log(np.maximum(gain_h1, gain_floor)) + (
            1 - posterior_presence
        ) * np.log(gain_floor)
        gain[:, frame] = np.exp(log_gain)
    smoothing = float(config["gain_smoothing"])
    if smoothing:
        for frame in range(1, gain.shape[1]):
            gain[:, frame] = smoothing * gain[:, frame - 1] + (1 - smoothing) * gain[:, frame]
    return np.clip(gain, gain_floor, 1.0)


def enhance(
    audio: np.ndarray,
    config: dict | None = None,
    speech_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, dict]:
    """Enhance mono 8 kHz audio, then gate non-speech and level VAD speech."""
    cfg = _validate_config(config or {})
    source = np.asarray(audio, dtype=np.float32).reshape(-1)
    if source.size == 0 or not np.all(np.isfinite(source)):
        raise ValueError("IMCRA-OM-LSA input must contain finite audio samples.")
    _, _, spectrum = signal.stft(
        source,
        fs=SAMPLE_RATE,
        window="hann",
        nperseg=WINDOW,
        noverlap=WINDOW - HOP,
        boundary="zeros",
        padded=True,
    )
    power = np.maximum(np.abs(spectrum).astype(np.float64) ** 2, np.finfo(np.float64).tiny)
    noise, presence = _imcra_noise_and_presence(power, cfg)
    gain = _omlsa_gain(power, noise, presence, cfg)
    _, enhanced = signal.istft(
        spectrum * gain,
        fs=SAMPLE_RATE,
        window="hann",
        nperseg=WINDOW,
        noverlap=WINDOW - HOP,
        input_onesided=True,
        boundary=True,
    )
    enhanced = enhanced[: source.size]
    if enhanced.size < source.size:
        enhanced = np.pad(enhanced, (0, source.size - enhanced.size))
    enhanced = np.asarray(enhanced, dtype=np.float64)
    if not np.all(np.isfinite(enhanced)):
        raise FloatingPointError("IMCRA-OM-LSA produced non-finite samples.")
    peak = float(np.max(np.abs(enhanced)))
    limit = float(cfg["peak_limit"])
    scale = min(1.0, limit / peak) if peak else 1.0
    enhanced *= scale

    voice_focus: dict[str, float | int | bool | str] = {
        "applied": False,
        "parameters_fitted": False,
        "reason": "speech VAD mask was not supplied",
    }
    if speech_mask is not None:
        frame_mask = np.asarray(speech_mask, dtype=bool).reshape(-1)
        sample_mask = np.repeat(frame_mask, 160)[: source.size]
        if sample_mask.size < source.size:
            sample_mask = np.pad(sample_mask, (0, source.size - sample_mask.size))
        if np.any(sample_mask):
            source_speech_rms = float(np.sqrt(np.mean(source[sample_mask].astype(np.float64) ** 2)))
            enhanced_speech_rms = float(np.sqrt(np.mean(enhanced[sample_mask] ** 2)))
            target_rms = 10 ** (float(cfg["speech_level_target_dbfs"]) / 20)
            maximum_gain = float(cfg["speech_gain_max"])
            speech_gain = min(maximum_gain, max(1.0, target_rms / max(enhanced_speech_rms, 1e-12)))
            gate_gain = float(cfg["noise_gate_gain"])
            radius = max(1, round(0.01 * SAMPLE_RATE))
            envelope = np.convolve(
                np.pad(sample_mask.astype(np.float64), (radius, radius), mode="edge"),
                np.ones(2 * radius + 1, dtype=np.float64) / (2 * radius + 1),
                mode="valid",
            )
            sample_gain = gate_gain + envelope * (speech_gain - gate_gain)
            enhanced *= sample_gain
            focused_peak = float(np.max(np.abs(enhanced)))
            focus_scale = min(1.0, limit / focused_peak) if focused_peak else 1.0
            enhanced *= focus_scale
            voice_focus = {
                "applied": True,
                "parameters_fitted": False,
                "speech_level_target_dbfs": float(cfg["speech_level_target_dbfs"]),
                "speech_gain_db": float(20 * np.log10(speech_gain)),
                "noise_gate_gain": gate_gain,
                "source_speech_rms": source_speech_rms,
                "enhanced_speech_rms_before_leveling": enhanced_speech_rms,
                "speech_frames": int(np.count_nonzero(frame_mask)),
                "peak_limit_scale": float(focus_scale),
            }
    enhanced = enhanced.astype(np.float32)
    return enhanced, {
        "gain_applied_db": float(
            20 * np.log10(max(np.sqrt(np.mean(enhanced.astype(np.float64) ** 2)), 1e-12)
                          / max(np.sqrt(np.mean(source.astype(np.float64) ** 2)), 1e-12))
        ),
        "algorithm": "simplified adaptive spectral gain + VAD-gated speech leveling",
        "sample_rate": SAMPLE_RATE,
        "input_samples": int(source.size),
        "output_samples": int(enhanced.size),
        "peak_limit_scale": float(scale),
        "mean_speech_presence": float(np.mean(presence)),
        "voice_focus": voice_focus,
        "parameters_fitted": False,
        "config": cfg,
    }


def observation_addition_sweep(
    enhanced: np.ndarray,
    original: np.ndarray,
    coefficients: Iterable[float] | None = None,
    peak_limit: float = 0.99,
) -> tuple[dict[float, np.ndarray], float]:
    """Create OA conditions using one shared limiter scale across the sweep.

    This is additive observation mixing, not convex interpolation. The common
    scale prevents the limiter from changing relative loudness between lambda
    conditions while ensuring no output clips on PCM export.
    """
    clean = np.asarray(enhanced, dtype=np.float64).reshape(-1)
    observed = np.asarray(original, dtype=np.float64).reshape(-1)
    if clean.shape != observed.shape:
        raise ValueError("Observation addition requires sample-aligned equal-length audio.")
    if not np.all(np.isfinite(clean)) or not np.all(np.isfinite(observed)):
        raise ValueError("Observation addition inputs must be finite.")
    lambdas = tuple(float(value) for value in (coefficients if coefficients is not None else DEFAULT_CONFIG["oa_sweep"]))
    if not lambdas or any(not 0 <= value <= 0.4 for value in lambdas):
        raise ValueError("OA coefficients must be a non-empty set of values in [0, 0.4].")
    if not 0 < peak_limit <= 1:
        raise ValueError("peak_limit must be in (0, 1].")
    mixtures = {value: clean + value * observed for value in lambdas}
    maximum_peak = max((float(np.max(np.abs(mix))) for mix in mixtures.values()), default=0.0)
    shared_scale = min(1.0, peak_limit / maximum_peak) if maximum_peak else 1.0
    outputs = {
        value: (mixture * shared_scale).astype(np.float32)
        for value, mixture in mixtures.items()
    }
    return outputs, shared_scale


def add_observation(
    enhanced: np.ndarray,
    original: np.ndarray,
    coefficient: float,
    peak_limit: float = 0.99,
    sweep: Iterable[float] | None = None,
) -> tuple[np.ndarray, dict]:
    """Apply one OA condition using the shared scale for the configured sweep."""
    configured = tuple(sweep or DEFAULT_CONFIG["oa_sweep"])
    if coefficient not in configured:
        configured = (*configured, float(coefficient))
    outputs, scale = observation_addition_sweep(enhanced, original, configured, peak_limit)
    return outputs[float(coefficient)], {
        "observation_addition_lambda": float(coefficient),
        "observation_addition_scale": scale,
        "observation_addition_mode": "enhanced + lambda * original",
    }
