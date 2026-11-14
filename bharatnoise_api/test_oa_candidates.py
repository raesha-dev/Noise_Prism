import csv
import json

import numpy as np
import pytest
import soundfile as sf

from bharatnoise_api.scripts.make_oa_candidates import generate


def _manifest(tmp_path, split="development"):
    audio = tmp_path / "source.wav"
    samples = (0.2 * np.sin(2 * np.pi * 300 * np.arange(8000) / 8000)).astype(np.float32)
    sf.write(audio, samples, 8000, subtype="PCM_16")
    manifest = tmp_path / "dev.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "audio_path", "language", "reference", "split"])
        writer.writeheader()
        writer.writerow({
            "id": "clip-1", "audio_path": str(audio), "language": "hi-IN",
            "reference": "synthetic speech", "split": split,
        })
    return manifest


def test_oa_candidates_record_lambda_and_unfitted_parameters(tmp_path):
    manifest = _manifest(tmp_path)
    output = tmp_path / "out"
    assert generate(manifest, output, [0.1]) == 1
    with (output / "C2_OA_lambda0.1.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    metadata = json.loads(row["processing_parameters"])
    samples, _ = sf.read(output / row["audio_path"], dtype="float32")
    assert row["candidate"] == "C2+OA(0.1)"
    assert float(row["oa_lambda"]) == 0.1
    assert metadata["parameters_fitted"] is False
    assert metadata["observation_lambda"] == 0.1
    assert np.max(np.abs(samples)) <= 0.99


def test_oa_candidates_refuse_held_out_rows(tmp_path):
    manifest = _manifest(tmp_path, split="held-out")
    with pytest.raises(ValueError, match="only be generated from development"):
        generate(manifest, tmp_path / "out", [0.1])
