"""Optional DeepFilterNet wrapper; the model is loaded once, lazily and safely."""
from __future__ import annotations

import threading

import numpy as np
from scipy import signal

_LOCK = threading.Lock()
_MODEL: tuple[object, object, int] | None = None
_ERROR: str | None = None


def _load():
    global _MODEL, _ERROR
    with _LOCK:
        if _MODEL is not None or _ERROR is not None:
            return _MODEL, _ERROR
        try:
            from df.enhance import enhance, init_df

            model, df_state, _ = init_df()
            _MODEL = (model, df_state, 48000)
            _MODEL_ENHANCE = enhance
            globals()["_MODEL_ENHANCE"] = _MODEL_ENHANCE
        except Exception as exc:
            _ERROR = f"{type(exc).__name__}: {exc}"
        return _MODEL, _ERROR


def availability() -> tuple[bool, str | None]:
    """Return whether both DeepFilterNet import and model initialization succeeded."""
    model, error = _load()
    return model is not None, error


def enhance_8k(x: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """Resample 8→48 kHz, enhance once the optional model is loaded, and return to 8 kHz."""
    model_data, error = _load()
    if model_data is None:
        raise RuntimeError(f"DeepFilterNet unavailable: {error or 'model load failed'}")
    model, df_state, _ = model_data
    source = np.asarray(x, dtype=np.float32)
    at_48k = signal.resample_poly(source, 6, 1).astype(np.float32)
    enhancer = globals().get("_MODEL_ENHANCE")
    if enhancer is None:
        raise RuntimeError("DeepFilterNet enhancer is unavailable.")
    try:
        import torch

        with torch.no_grad():
            processed = enhancer(model, df_state, torch.from_numpy(at_48k))
        enhanced_48k = processed.detach().cpu().numpy().reshape(-1).astype(np.float32)
    except Exception as exc:
        raise RuntimeError(f"DeepFilterNet enhancement failed: {type(exc).__name__}") from exc
    output = signal.resample_poly(enhanced_48k, 1, 6).astype(np.float32)[:len(source)]
    if len(output) < len(source):
        output = np.pad(output, (0, len(source) - len(output)))
    peak = float(np.max(np.abs(output))) if output.size else 0.0
    output *= min(1.0, 0.99 / max(peak, 1e-12))
    input_rms = float(np.sqrt(np.mean(source.astype(np.float64) ** 2))) if source.size else 0.0
    output_rms = float(np.sqrt(np.mean(output.astype(np.float64) ** 2))) if output.size else 0.0
    gain = 20 * np.log10(max(output_rms, 1e-12) / max(input_rms, 1e-12))
    return output, {"gain_applied_db": float(gain)}
