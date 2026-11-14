import csv

import numpy as np
import soundfile as sf

from bharatnoise_api.scripts.select_hardest import select_hardest
from bharatnoise_api.scripts.select_manifest import select


def _write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_manifest_selection_uses_explicit_filters_and_absolute_audio_paths(tmp_path):
    audio = tmp_path / "clip.wav"
    sf.write(audio, np.zeros(800, dtype=np.float32), 8000, subtype="PCM_16")
    manifest = tmp_path / "all.csv"
    _write(manifest, ["id", "audio_path", "split", "group", "clean_reference_available"], [
        {"id": "keep", "audio_path": "clip.wav", "split": "dev", "group": "clean_control",
         "clean_reference_available": "true"},
        {"id": "drop", "audio_path": "clip.wav", "split": "test", "group": "staged_call",
         "clean_reference_available": "false"},
    ])
    rows = select(manifest, tmp_path / "selected.csv", "dev", "clean_control", True)
    assert [row["id"] for row in rows] == ["keep"]
    assert rows[0]["audio_path"] == str(audio.resolve())


def test_hardest_clip_selection_ranks_failures_then_highest_wer(tmp_path):
    audio = tmp_path / "clip.wav"
    sf.write(audio, np.zeros(800, dtype=np.float32), 8000, subtype="PCM_16")
    manifest = tmp_path / "dev.csv"
    fields = ["id", "audio_path", "reference", "split"]
    _write(manifest, fields, [
        {"id": "good", "audio_path": "clip.wav", "reference": "a b", "split": "dev"},
        {"id": "wrong", "audio_path": "clip.wav", "reference": "a b", "split": "dev"},
        {"id": "empty", "audio_path": "clip.wav", "reference": "a b", "split": "dev"},
    ])
    results = tmp_path / "c0.csv"
    _write(results, ["id", "reference", "hypothesis", "split", "candidate", "error"], [
        {"id": "good", "reference": "a b", "hypothesis": "a b", "split": "development", "candidate": "C0", "error": ""},
        {"id": "wrong", "reference": "a b", "hypothesis": "x y", "split": "development", "candidate": "C0", "error": ""},
        {"id": "empty", "reference": "a b", "hypothesis": "", "split": "development", "candidate": "C0", "error": ""},
    ])
    selected = select_hardest(manifest, results, tmp_path / "hard.csv", 2)
    assert [row["id"] for row in selected] == ["empty", "wrong"]
