"""Numerical and signal-shape tests for experimental spectral enhancement and OA."""
from __future__ import annotations

import numpy as np
import pytest

from .audio.imcra_omlsa import (
    DEFAULT_CONFIG,
    add_observation,
    enhance,
    observation_addition_sweep,
)


def _speech_and_noise(seconds: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(42)
    time_axis = np.arange(round(8000 * seconds)) / 8000
    speech = 0.15 * np.sin(2 * np.pi * 440 * time_axis)
    noise = 0.025 * rng.standard_normal(time_axis.size)
    return (speech + noise).astype(np.float32)


def test_imcra_omlsa_preserves_length_finiteness_and_is_deterministic():
    source = _speech_and_noise()
    first, metadata = enhance(source)
    second, _ = enhance(source)

    assert first.shape == source.shape
    assert np.isfinite(first).all()
    assert np.array_equal(first, second)
    assert metadata["sample_rate"] == 8000
    assert metadata["input_samples"] == metadata["output_samples"] == source.size
    assert metadata["algorithm"] == "simplified adaptive spectral gain + VAD-gated speech leveling"


def test_vad_voice_focus_levels_speech_and_attenuates_non_speech():
    sample_rate = 8000
    time_axis = np.arange(sample_rate * 2) / sample_rate
    active = (time_axis >= 0.5) & (time_axis < 1.5)
    rng = np.random.default_rng(42)
    source = 0.02 * rng.standard_normal(time_axis.size)
    source[active] += 0.08 * np.sin(2 * np.pi * 440 * time_axis[active])
    mask = np.zeros(100, dtype=bool)
    mask[25:75] = True

    enhanced, metadata = enhance(source.astype(np.float32), speech_mask=mask)
    input_speech_rms = float(np.sqrt(np.mean(source[active] ** 2)))
    output_speech_rms = float(np.sqrt(np.mean(enhanced[active] ** 2)))
    input_quiet_rms = float(np.sqrt(np.mean(source[~active] ** 2)))
    output_quiet_rms = float(np.sqrt(np.mean(enhanced[~active] ** 2)))

    assert output_speech_rms >= input_speech_rms * 0.7
    assert output_quiet_rms <= input_quiet_rms * 0.2
    assert np.max(np.abs(enhanced)) <= 0.99
    assert metadata["voice_focus"]["applied"] is True
    assert metadata["parameters_fitted"] is False


def test_imcra_omlsa_handles_silence_and_low_energy_inputs():
    for source in (np.zeros(2400, np.float32), np.full(2400, 1e-8, np.float32)):
        output, _ = enhance(source)
        assert output.shape == source.shape
        assert np.isfinite(output).all()
        assert np.max(np.abs(output)) <= 0.99


def test_imcra_omlsa_handles_non_multiple_of_hop_sample_count():
    source = _speech_and_noise(0.473)
    output, _ = enhance(source)
    assert output.size == source.size


def test_oa_sweep_is_additive_and_uses_a_shared_peak_scale():
    original = np.full(32, 0.8, dtype=np.float32)
    enhanced = np.full(32, 0.7, dtype=np.float32)
    coefficients = [0.0, 0.1, 0.4]
    outputs, scale = observation_addition_sweep(enhanced, original, coefficients)

    assert scale < 1
    assert all(np.max(np.abs(value)) <= 0.99 for value in outputs.values())
    np.testing.assert_allclose(outputs[0.0], outputs[0.1] - 0.1 * original * scale)
    np.testing.assert_allclose(outputs[0.0], enhanced * scale)
    assert not np.shares_memory(outputs[0.1], original)


def test_oa_matches_enhanced_at_zero_lambda_and_records_configuration():
    enhanced = np.linspace(-0.2, 0.2, 101, dtype=np.float32)
    original = _speech_and_noise(101 / 8000)
    output, metadata = add_observation(enhanced, original, 0.0)

    assert np.array_equal(output, enhanced)
    assert metadata["observation_addition_lambda"] == 0.0
    assert metadata["observation_addition_mode"] == "enhanced + lambda * original"


@pytest.mark.parametrize("coefficient", [-0.01, 0.41])
def test_oa_rejects_out_of_range_coefficients(coefficient):
    with pytest.raises(ValueError, match=r"\[0, 0.4\]"):
        observation_addition_sweep(np.zeros(4), np.zeros(4), [coefficient])


def test_default_oa_sweep_contains_requested_experimental_grid():
    assert DEFAULT_CONFIG["oa_sweep"] == [0.0, 0.05, 0.1, 0.2, 0.3, 0.4]
