import csv
import json
from collections import Counter

import numpy as np
import pytest
import soundfile as sf

from .scripts.build_eval_set import build_balanced
from .scripts.data_common import MANIFEST_FIELDS, write_manifest


def _write_inputs(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    rows = []
    sample_rate = 8000
    axis = np.arange(2 * sample_rate) / sample_rate
    for index in range(6):
        clip_id = f"clip-{index}"
        path = base_dir / f"{clip_id}.wav"
        speech = 0.1 * np.sin(2 * np.pi * (180 + index * 10) * axis)
        sf.write(path, speech, sample_rate, subtype="PCM_16")
        rows.append({
            "id": clip_id,
            "source_clip_id": clip_id,
            "audio_path": path.name,
            "language": "en-IN",
            "reference": f"fixture transcript {index}",
            "speaker_id": "",
            "group": "clean_control",
            "source": "synthetic_test_fixture",
            "source_type": "synthetic_test_fixture",
            "base_source_type": "",
            "license": "synthetic test fixture",
            "reference_provenance": "test fixture",
            "native_sample_rate": sample_rate,
            "split": "dev",
            "split_basis": "test_fixture",
            "clean_reference_available": True,
            "noise_type": "",
            "snr_db_nominal": "",
            "snr_db_measured": "",
            "snr_basis": "",
        })
    manifest = base_dir / "base.csv"
    write_manifest(manifest, rows)

    noise_dir = tmp_path / "noise"
    licenses = []
    time_axis = np.arange(10 * sample_rate) / sample_rate
    for noise_type, frequency in (("traffic", 310), ("babble", 720)):
        for split, offset in (("dev", 0), ("test", 0.2)):
            path = noise_dir / noise_type / split / "scene.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            samples = 0.2 * np.sin(2 * np.pi * frequency * time_axis + offset)
            sf.write(path, samples, sample_rate, subtype="PCM_16")
            licenses.append({
                "file": f"{noise_type}/{split}/scene.wav",
                "type": noise_type,
                "source": "synthetic fixture",
                "license": "test fixture",
                "note": "local test only",
            })
    noise_dir.mkdir(exist_ok=True)
    with (noise_dir / "LICENSES.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file", "type", "source", "license", "note"])
        writer.writeheader()
        writer.writerows(licenses)
    return manifest, noise_dir


def test_balanced_builder_uses_license_manifest_and_balances_cells(tmp_path):
    manifest, noise_dir = _write_inputs(tmp_path)
    output = tmp_path / "eval"
    result = build_balanced(manifest, noise_dir, output, mixtures_per_clip=2, seed=13)
    dev_rows = result["rows"]["dev"]
    assert len(dev_rows) == 12
    assert not (output / "test.csv").exists()
    assert len({row["source_clip_id"] for row in dev_rows}) == 6
    assert {row["source_type"] for row in dev_rows} == {"synthetic_mixture"}
    assert all(row["clean_reference_available"] is True for row in dev_rows)
    assert all(row["snr_basis"] == "measured_vs_clean" for row in dev_rows)
    cells = Counter((row["noise_type"], float(row["snr_db_nominal"])) for row in dev_rows)
    assert set(cells.values()) == {2}
    for row in dev_rows:
        assert float(row["snr_db_measured"]) == pytest.approx(
            float(row["snr_db_nominal"]), abs=0.1
        )
    log = json.loads((output / "build_log.json").read_text(encoding="utf-8"))
    assert log["independent_source_clips"] == {"dev": {"en-IN": 6}}
    assert all(row["si_sdr_db"] is not None for row in log["sources"])


def test_balanced_builder_rejects_unlicensed_audio_and_non_clean_rows(tmp_path):
    manifest, noise_dir = _write_inputs(tmp_path)
    stray = noise_dir / "traffic" / "dev" / "unlisted.wav"
    sf.write(stray, np.zeros(8000, dtype=np.float32), 8000)
    with pytest.raises(ValueError, match="Refusing unlicensed noise files"):
        build_balanced(manifest, noise_dir, tmp_path / "out")

    stray.unlink()
    rows = []
    with manifest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["clean_reference_available"] = "false"
    rejected = tmp_path / "non-clean.csv"
    with rejected.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="No explicitly clean-reference-eligible"):
        build_balanced(rejected, noise_dir, tmp_path / "out")


def test_balanced_builder_accepts_relative_noise_directory(tmp_path, monkeypatch):
    manifest, noise_dir = _write_inputs(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = build_balanced(manifest, noise_dir.relative_to(tmp_path), tmp_path / "eval")

    assert len(result["rows"]["dev"]) == 12
    assert all(
        (tmp_path / "noise" / entry["noise_license_file"]).is_file()
        for entry in json.loads((tmp_path / "eval" / "build_log.json").read_text())["sources"]
    )
