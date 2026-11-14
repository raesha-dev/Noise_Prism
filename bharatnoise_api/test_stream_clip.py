import argparse

import numpy as np
import pytest
import soundfile as sf

from bharatnoise_api.scripts.stream_clip import load_pcm_frames, upgrade_headers


def test_stream_frames_are_exact_size_and_include_final_silence(tmp_path):
    source = tmp_path / "short.wav"
    sf.write(source, np.zeros(321, dtype=np.float32), 8000, subtype="PCM_16")
    frames, duration = load_pcm_frames(source)
    assert duration == 321 / 8000
    assert frames
    assert all(len(frame) == 1024 for frame in frames)
    assert len(frames) >= 16


def test_stream_headers_use_only_documented_vad_names():
    args = argparse.Namespace(
        vad_threshold=0.6, min_speech_ms=250, min_silence_ms=450,
        min_segment_ms=100, max_speech_s=12,
    )
    headers = upgrade_headers("secret", "indic-auto", "v3.0", args)
    assert set(headers) == {
        "x-api-key-id", "language_code", "model", "x-sample-rate",
        "x-vad-threshold", "x-min-speech-ms", "x-min-silence-ms",
        "x-min-segment-ms", "x-max-speech-s",
    }
    assert headers["x-api-key-id"] == "secret"
    with pytest.raises(ValueError, match="only documented"):
        upgrade_headers("secret", "indic-auto", "v2.5", args)
