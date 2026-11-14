import csv
import wave
from pathlib import Path

import numpy as np

from bharatnoise_api.gnani_client import Transcript
from bharatnoise_api.scripts.diagnose_clips import (
    diagnose,
    load_manifest,
    write_condition_results,
    write_results,
)


class FakeClient:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def transcribe(self, path, language, *, model, fmt, run_id):
        self.calls.append((language, model, fmt, run_id))
        return next(self.results)


def _wav(path: Path, duration: float = 1.0) -> None:
    samples = (np.zeros(round(8000 * duration))).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(samples.tobytes())


def test_diagnose_uses_c0_conditions_and_never_stores_transcript(tmp_path):
    audio = tmp_path / "c0.wav"
    _wav(audio)
    manifest = tmp_path / "clips.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "audio_path", "language", "reference"])
        writer.writeheader()
        writer.writerow({"id": "clip-1", "audio_path": str(audio), "language": "hi-IN", "reference": "hello world"})
    rows = load_manifest(manifest)
    client = FakeClient([
        Transcript(ok=True, text="hello world", detected_language_code="hi-IN"),
        Transcript(ok=True, text="hello there this is a mismatch", detected_language_code="kn-IN"),
        Transcript(ok=True, text="", model="prisma-v3.0"),
    ])
    results = diagnose(rows, client, ["v25-correct", "v30-correct", "v30-auto"], "test-run", 0.5)
    output = tmp_path / "diagnosis.csv"
    write_results(output, results)
    contents = output.read_text(encoding="utf-8")
    assert [row["failure_label"] for row in results] == [
        "TEXT_RETURNED", "LANGUAGE_MISMATCH", "EMPTY",
    ]
    assert [call[:3] for call in client.calls] == [
        ("hi-IN", "v2.5", "verbatim"),
        ("hi-IN", "v3.0", "verbatim"),
        ("indic-auto", "v3.0", "verbatim"),
    ]
    assert all(row["diagnosis_thresholds_fitted"] == "false" for row in results)
    assert "hello world" not in contents
    assert results[2]["failure_label"] == "EMPTY"

    condition_files = write_condition_results(tmp_path / "c0_models.csv", results)
    assert set(condition_files) == {"v25-correct", "v30-correct", "v30-auto"}
    for condition, path in condition_files.items():
        with path.open(newline="", encoding="utf-8") as handle:
            saved_rows = list(csv.DictReader(handle))
        assert saved_rows
        assert {row["condition"] for row in saved_rows} == {condition}
