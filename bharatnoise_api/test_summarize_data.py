import csv

import numpy as np
import soundfile as sf

from bharatnoise_api.scripts.summarize_data import summarize


def test_summary_reports_only_aggregates_and_creates_provenance(tmp_path):
    data = tmp_path / "data"
    manifests = data / "manifests"
    audio_dir = data / "audio"
    manifests.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    audio = audio_dir / "clip.wav"
    sf.write(audio, np.zeros(8000, dtype=np.float32), 8000, subtype="PCM_16")
    manifest = manifests / "clips.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "id", "source_clip_id", "audio_path", "source", "source_type",
            "language", "split", "speaker_id",
        ])
        writer.writeheader()
        writer.writerow({
            "id": "clip-1", "source_clip_id": "source-1",
            "audio_path": "../audio/clip.wav", "source": "fixture",
            "source_type": "synthetic", "language": "hi-IN", "split": "dev",
            "speaker_id": "spk_a",
        })
    derived = manifests / "clean_control_dev.csv"
    derived.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    result = summarize(data)
    assert result["total_rows"] == 1
    assert result["independent_source_clips"]["dev"] == 1
    assert result["minutes_by_split"]["dev"] == 1 / 60
    provenance = (data / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "clip-1" not in provenance
    assert "synthetic" in provenance
