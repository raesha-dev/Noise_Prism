"""Audio I/O, analysis, candidate versions, speech-preservation guard, router, consensus.

Everything here is deliberately small and dependency-light so the API runs on a laptop.

Placeholders you are expected to replace as the project matures:
  * `energy_vad` is a simple adaptive energy detector, not a trained VAD. Swap in
    Silero VAD (or WebRTC VAD) behind the same `analyze` output once you have it.
  * Only C0 (original) and C1 (hygiene) exist. Register C2 (blended suppression) and
    C3 (neural) with `register(...)` when they are built; the API picks them up.
  * The router thresholds in config/router.json are UNFITTED until you fit them on the
    development set and flip "fitted" to true. The API reports that flag in every
    decision so an unfitted router can never pass as a validated one.
"""
from __future__ import annotations

import io
import json
import threading
from dataclasses import dataclass
from math import gcd
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np
import soundfile as sf
from scipy import signal

try:
    from .audio import hygiene as hygiene_module
    from .audio import neural, suppress as suppress_module, vad
    from .metrics import align_counts, normalize
except ImportError:
    from audio import hygiene as hygiene_module
    from audio import neural, suppress as suppress_module, vad
    from metrics import align_counts, normalize

SR = 8000
MAX_SECONDS = 30.0
MIN_SECONDS = 0.3
FRAME = 160
ROOT = Path(__file__).resolve().parent
SUPPRESS_CONFIG = suppress_module.load_config()


class AudioError(ValueError):
    """The uploaded audio cannot be used (undecodable, too long, too short)."""


# ------------------------------------------------------------------ audio I/O
def load_audio(data: bytes) -> np.ndarray:
    """Decode WAV / FLAC / OGG (MP3 if libsndfile supports it) to mono float32 at 8 kHz."""
    try:
        x, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:  # soundfile raises several error types
        raise AudioError("Could not decode the audio. Use WAV, FLAC or OGG.") from exc
    x = x.mean(axis=1)
    if int(sr) != SR:
        g = gcd(int(sr), SR)
        x = signal.resample_poly(x, SR // g, int(sr) // g).astype(np.float32)
    seconds = len(x) / SR
    if seconds > MAX_SECONDS:
        raise AudioError(f"Clip is {seconds:.1f} s; the limit is {MAX_SECONDS:.0f} s.")
    if seconds < MIN_SECONDS:
        raise AudioError(f"Clip is {seconds:.2f} s; at least {MIN_SECONDS} s is needed.")
    return np.clip(x, -1.0, 1.0)


def to_wav_bytes(x: np.ndarray) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, x, SR, format="WAV", subtype="PCM_16")
    return buf.getvalue()


# ------------------------------------------------------------------- analysis
def _frames(x: np.ndarray) -> np.ndarray:
    n = len(x) // FRAME
    return x[: n * FRAME].reshape(n, FRAME)


def energy_vad(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Compatibility wrapper for the adaptive energy detector."""
    return vad.adaptive_energy(x, FRAME)


def analyze(x: np.ndarray) -> dict:
    mask, vad_name, vad_reason = vad.speech_mask(x, SR, FRAME)
    _, power = energy_vad(x)
    db = 10 * np.log10(power)
    noise_frames = db <= np.percentile(db, 20)
    noise_p = float(power[noise_frames].mean())
    snr: Optional[float] = None
    if mask.any():
        speech_p = float(power[mask].mean())
        snr = float(np.clip(10 * np.log10(max(speech_p - noise_p, 1e-12) / noise_p), -20.0, 60.0))
    segments, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            segments.append([round(start * FRAME / SR, 3), round(i * FRAME / SR, 3)])
            start = None
    speech_p = float(power[mask].mean()) if mask.any() else None
    spectrum = signal.welch(x, fs=SR, nperseg=min(512, len(x)))[1]
    spectrum = np.array_split(spectrum, 32)
    spectrum_bins = [float(np.mean(part)) if len(part) else 0.0 for part in spectrum]
    peak_spectrum = max(spectrum_bins, default=0.0)
    return {
        "duration_s": round(len(x) / SR, 3),
        "noise_floor_db": round(float(10 * np.log10(noise_p)), 1),
        "snr_db_est": None if snr is None else round(snr, 1),
        "speech_energy_db": None if speech_p is None else round(float(10 * np.log10(speech_p)), 1),
        "speech_ratio": round(float(mask.mean()), 3),
        "clipped_ratio": round(float(np.mean(np.abs(x) >= 0.999)), 4),
        "segments": segments,
        "vad": vad_name,
        "vad_unavailable_reason": vad_reason,
        "spectrum": [round(v / peak_spectrum, 5) if peak_spectrum else 0.0 for v in spectrum_bins],
        "snr_note": "estimate from frame energies: speech frames versus the quietest 20% of frames",
    }


def speech_mask_samples(x: np.ndarray) -> np.ndarray:
    mask, _ = energy_vad(x)
    return np.repeat(mask, FRAME)


# ----------------------------------------------------------------- candidates
_BANDPASS = hygiene_module._BANDPASS


def hygiene(x: np.ndarray) -> np.ndarray:
    """C1: compatibility wrapper around the full hygiene operation."""
    return hygiene_module.hygiene(x)[0]


@dataclass(frozen=True)
class Candidate:
    id: str
    label: str
    fn: Callable[[np.ndarray], np.ndarray]


CANDIDATES: dict[str, Candidate] = {
    "C0": Candidate("C0", "Original audio", lambda x: x),
    "C1": Candidate("C1", "Hygiene only (300-3400 Hz band-limit, level)", hygiene),
    "C2": Candidate(
        "C2",
        "Conservative Wiener suppression with observation blend",
        lambda x: suppress_module.suppress(x, speech_mask_samples(x)[:len(x) // FRAME], analyze(x)["snr_db_est"], SUPPRESS_CONFIG)[0],
    ),
}
PLANNED: dict[str, str] = {"C3": "DeepFilterNet neural enhancement"}
UNAVAILABLE_REASONS: dict[str, str] = {}
_CANDIDATE_META: dict[str, Callable[[np.ndarray], dict]] = {
    "C0": lambda x: {"gain_applied_db": 0.0},
    "C1": lambda x: hygiene_module.hygiene(x)[1],
    "C2": lambda x: suppress_module.suppress(
        x, speech_mask_samples(x)[:len(x) // FRAME], analyze(x)["snr_db_est"], SUPPRESS_CONFIG
    )[1],
}
_CANDIDATE_APPLY: dict[str, Callable[[np.ndarray], tuple[np.ndarray, dict]]] = {
    "C0": lambda x: (x.copy(), {"gain_applied_db": 0.0}),
    "C1": hygiene_module.hygiene,
    "C2": lambda x: suppress_module.suppress(
        x, speech_mask_samples(x)[:len(x) // FRAME], analyze(x)["snr_db_est"], SUPPRESS_CONFIG
    ),
}


def register(candidate: Candidate) -> None:
    """Call this from your C2 / C3 modules so the API exposes and uses them."""
    CANDIDATES[candidate.id] = candidate


def initialize_neural() -> tuple[bool, str | None]:
    """Register C3 only after optional imports and the model itself are available."""
    available, reason = neural.availability()
    if available:
        CANDIDATES["C3"] = Candidate("C3", "DeepFilterNet neural enhancement", lambda x: neural.enhance_8k(x)[0])
        _CANDIDATE_META["C3"] = lambda x: neural.enhance_8k(x)[1]
        _CANDIDATE_APPLY["C3"] = neural.enhance_8k
        UNAVAILABLE_REASONS.pop("C3", None)
    else:
        UNAVAILABLE_REASONS["C3"] = reason or "DeepFilterNet model could not be loaded."
    return available, reason


def apply_candidate(candidate_id: str, x: np.ndarray, suppress_config: Optional[dict] = None) -> tuple[np.ndarray, dict]:
    """Run one registered candidate and return its measured processing metadata."""
    if candidate_id == "C2" and suppress_config is not None:
        audio, meta = suppress_module.suppress(
            x, speech_mask_samples(x)[:len(x) // FRAME], analyze(x)["snr_db_est"], suppress_config
        )
    elif candidate_id in _CANDIDATE_APPLY:
        audio, meta = _CANDIDATE_APPLY[candidate_id](x)
    else:
        audio = CANDIDATES[candidate_id].fn(x)
        meta = _CANDIDATE_META.get(candidate_id, lambda _: {"gain_applied_db": 0.0})(x)
    audio = np.asarray(audio, dtype=np.float32)
    if not np.all(np.isfinite(audio)):
        raise ValueError(f"{candidate_id} produced non-finite audio.")
    if candidate_id == "C2":
        config = suppress_config or SUPPRESS_CONFIG
        meta["parameters_fitted"] = bool(config.get("fitted", False))
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1.0:
        audio = (audio / peak * 0.99).astype(np.float32)
    return audio, meta


class StreamBandpass:
    """Causal, stateful version of C1's band-limit for the live WebSocket path (int16 bytes in and out)."""

    def __init__(self) -> None:
        self._zi = np.zeros((_BANDPASS.shape[0], 2))
        self._carry = b""

    def process(self, pcm16: bytes) -> bytes:
        data = self._carry + pcm16
        usable = len(data) - (len(data) % 2)
        self._carry = data[usable:]
        x = np.frombuffer(data[:usable], dtype="<i2").astype(np.float64) / 32768.0
        y, self._zi = signal.sosfilt(_BANDPASS, x, zi=self._zi)
        return (np.clip(y, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


# ---------------------------------------------------------------------- guard
def guard(original: np.ndarray, candidate: np.ndarray, meta: Optional[dict] = None, thresholds: Optional[dict] = None) -> dict:
    """Measure gain-invariant spectral distortion and compensated speech-energy change."""
    n = min(len(original), len(candidate))
    sm = speech_mask_samples(original)[:n].astype(bool)
    if sm.sum() < 1024:
        return {"checked": False, "reason": "too little speech detected to check",
                "lsd_db": None, "speech_energy_change_db": None, "passed": None}
    original_speech = original[:n][sm]
    candidate_speech = candidate[:n][sm]
    f, p_o = signal.welch(original_speech, fs=SR, nperseg=256)
    _, p_c = signal.welch(candidate_speech, fs=SR, nperseg=256)
    band = (f >= 300) & (f <= 3400)
    d = 10 * np.log10(p_c[band] + 1e-12) - 10 * np.log10(p_o[band] + 1e-12)
    d -= d.mean()
    original_power = float(np.mean(original_speech.astype(np.float64) ** 2)) + 1e-12
    candidate_power = float(np.mean(candidate_speech.astype(np.float64) ** 2)) + 1e-12
    gain_db = float((meta or {}).get("gain_applied_db", 0.0))
    energy_change = 10 * np.log10(candidate_power / original_power) - gain_db
    limits = thresholds or {}
    max_lsd = float(limits.get("max_lsd_db", 6.0))
    max_energy = float(limits.get("max_speech_energy_change_db", 12.0))
    lsd = float(np.sqrt(np.mean(d ** 2)))
    passed = bool(lsd <= max_lsd and abs(energy_change) <= max_energy)
    return {"checked": True, "lsd_db": round(lsd, 2),
            "speech_energy_change_db": round(float(energy_change), 2),
            "gain_applied_db": round(gain_db, 2), "max_lsd_db": max_lsd,
            "max_speech_energy_change_db": max_energy, "passed": passed}


# --------------------------------------------------------------------- router
def load_router_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.setdefault("fitted", False)
    cfg.setdefault("guard", {"max_lsd_db": 6.0, "max_speech_energy_change_db": 12.0})
    return cfg


def route(analysis: dict, available: Sequence[str], cfg: dict) -> tuple[str, str]:
    snr = analysis.get("snr_db_est")
    if snr is None:
        return ("C0", "no speech detected, so the original audio is sent") if "C0" in available else (available[0], "no speech detected; using the first available variant")
    for rule in cfg["rules"]:
        if snr >= rule["min_snr_db"]:
            wanted = rule["variant"]
            if wanted in available:
                return wanted, f"estimated SNR {snr:.1f} dB, rule selects {wanted}"
            for fallback in cfg.get("fallback_chain", []):
                if fallback in available:
                    return fallback, f"estimated SNR {snr:.1f} dB, {wanted} is not built yet, using {fallback}"
            fallback = "C0" if "C0" in available else available[0]
            return fallback, f"estimated SNR {snr:.1f} dB, {wanted} is unavailable, using {fallback}"
    fallback = "C0" if "C0" in available else available[0]
    return fallback, "no routing rule matched; using the original" if fallback == "C0" else f"no routing rule matched; using {fallback}"


# ------------------------------------------------------------------ consensus
def consensus_pick(hypotheses: dict[str, str], priority: Sequence[str]) -> dict:
    """Minimum-Bayes-risk choice: the hypothesis with the smallest summed word edit distance to the others.

    Needs no confidence scores. Ties go to the earliest key in `priority`.
    """
    keys = list(hypotheses)
    tokens = {k: normalize(hypotheses[k]).split() for k in keys}
    dist = {a: {b: (0 if a == b else align_counts(tokens[a], tokens[b]).errors) for b in keys} for a in keys}
    totals = {a: sum(dist[a].values()) for a in keys}
    order = {k: (priority.index(k) if k in priority else len(priority)) for k in keys}
    chosen = min(keys, key=lambda k: (totals[k], order[k]))
    return {"chosen": chosen, "total_distance": totals, "pairwise_distance": dist}


# ----------------------------------------------------------- scoring / diffing
def align_ops(ref: Sequence[str], hyp: Sequence[str]) -> list[dict]:
    """Word-level alignment for highlighting: ok / sub / del / ins."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = i
    for j in range(1, m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]), d[i - 1][j] + 1, d[i][j - 1] + 1)
    ops, i, j = [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            ops.append({"op": "ok" if ref[i - 1] == hyp[j - 1] else "sub", "ref": ref[i - 1], "hyp": hyp[j - 1]})
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            ops.append({"op": "del", "ref": ref[i - 1], "hyp": None})
            i -= 1
        else:
            ops.append({"op": "ins", "ref": None, "hyp": hyp[j - 1]})
            j -= 1
    return ops[::-1]


# ---------------------------------------------------------------- spectrogram
_PLOT_LOCK = threading.Lock()  # matplotlib is not thread-safe


def spectrogram_png(x: np.ndarray) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    f, t, s = signal.spectrogram(x, fs=SR, nperseg=256, noverlap=192)
    db = 10 * np.log10(s + 1e-10)
    with _PLOT_LOCK:
        fig, ax = plt.subplots(figsize=(6, 2.2), dpi=100)
        ax.pcolormesh(t, f, db, shading="auto", cmap="magma", vmin=db.max() - 70, vmax=db.max())
        ax.set_xlabel("seconds")
        ax.set_ylabel("Hz")
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
    return buf.getvalue()
