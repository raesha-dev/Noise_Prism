import csv

import numpy as np
import soundfile as sf

from .scripts.data_common import write_manifest
from .scripts.validate_snr_estimators import TARGET_SNRS_DB, validate


def test_snr_validation_uses_dev_clean_references_and_never_auto_validates(tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    source = audio_dir / "speech.wav"
    sample_rate = 8000
    t = np.arange(2 * sample_rate, dtype=np.float32) / sample_rate
    sf.write(source, 0.1 * np.sin(2 * np.pi * 220 * t), sample_rate)
    manifest = tmp_path / "clean.csv"
    write_manifest(manifest, [{
        "id": "source-1",
        "source_clip_id": "source-1",
        "audio_path": f"audio/{source.name}",
        "language": "en-IN",
        "reference": "fixture",
        "speaker_id": "spk_fixture",
        "group": "clean_control",
        "source": "synthetic_test_fixture",
        "source_type": "synthetic_tts",
        "base_source_type": "",
        "license": "fixture",
        "reference_provenance": "fixture",
        "native_sample_rate": sample_rate,
        "split": "dev",
        "split_basis": "fixture",
        "clean_reference_available": True,
    }])

    noise_dir = tmp_path / "noise"
    license_rows = []
    noise_t = np.arange(10 * sample_rate, dtype=np.float32) / sample_rate
    for noise_type, frequency in (("traffic", 310), ("babble", 720)):
        path = noise_dir / noise_type / "dev" / "scene.wav"
        path.parent.mkdir(parents=True)
        sf.write(path, 0.1 * np.sin(2 * np.pi * frequency * noise_t), sample_rate)
        license_rows.append({
            "file": f"{noise_type}/dev/scene.wav",
            "type": noise_type,
            "source": "synthetic fixture",
            "license": "test fixture",
            "note": "local test only",
        })
    with (noise_dir / "LICENSES.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file", "type", "source", "license", "note"])
        writer.writeheader()
        writer.writerows(license_rows)

    results = validate(manifest, noise_dir)
    stationary = [row for row in results if row["estimator"] == "stationary_snr_db_est"]
    wada = [row for row in results if row["estimator"] == "wada_snr_db"]

    assert len(stationary) == 2 * len(TARGET_SNRS_DB)
    assert all(row["source_clips"] == 1 and row["validated"] is False for row in stationary)
    assert len(wada) == 2
    assert all(row["mean_absolute_error_db"] is None for row in wada)
