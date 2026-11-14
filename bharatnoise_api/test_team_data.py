import csv
import unicodedata
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from .scripts.audit_team_audio import audit_team_audio
from .scripts.import_team_recordings import import_team
from .scripts.team_data import validate_team_data


LANGUAGES = (("hi", "hi-IN"), ("kn", "kn-IN"), ("en", "en-IN"))


def _write_team(team_dir: Path, *, complete: bool = True, one_speaker: bool = False) -> None:
    audio_dir = team_dir / "audio"
    audio_dir.mkdir(parents=True)
    script_rows = []
    review_rows = []
    for language_prefix, language in LANGUAGES:
        for sentence in range(1, 19):
            clip_id = f"{language_prefix}_{sentence:02d}"
            filename = "café clip.wav" if clip_id == "hi_01" else f"nonpattern-{clip_id}.wav"
            filename = unicodedata.normalize("NFC", filename)
            rate = 24000 if clip_id == "hi_01" else 48000
            time_axis = np.arange(rate // 5, dtype=np.float32) / rate
            samples = 0.1 * np.sin(2 * np.pi * 180 * time_axis)
            sf.write(audio_dir / filename, samples, rate, subtype="PCM_16")
            speaker = "spk_a" if one_speaker or sentence % 2 else "spk_b"
            split = ("dev" if sentence % 2 else "test") if complete else ""
            source_type = "synthetic_tts" if complete else ""
            text = f"synthetic script {language} {sentence}"
            script_rows.append({
                "id": clip_id,
                "language": language,
                "text": text,
                "filename": filename,
                "speaker_id": "",
                "split": "",
                "voice_group_suggestion": f"{language_prefix}_candidate_01",
                "voice_group_status": "PROVISIONAL",
            })
            review_rows.append({
                "id": clip_id,
                "language": language,
                "filename": filename,
                "reference_text": text,
                "voice_group_suggestion": f"{language_prefix}_candidate_01",
                "speaker_id_confirmed": speaker,
                "split": split,
                "source_type": source_type,
                "quiet_room": "",
                "notes": "",
            })
    for name, rows in (
        ("script_with_voice_filenames.csv", script_rows),
        ("voice_group_review.csv", review_rows),
    ):
        with (team_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def test_team_validator_and_importer_use_reviewed_labels_and_nfc_filenames(tmp_path):
    team_dir = tmp_path / "team"
    _write_team(team_dir)
    validation = validate_team_data(team_dir)
    assert not validation.issues
    assert len(validation.audio_by_name) == 54
    manifest_path = tmp_path / "data" / "manifests" / "team.csv"
    rows = import_team(
        team_dir,
        tmp_path / "data" / "audio" / "team",
        manifest_path,
    )
    assert len(rows) == 54
    assert {row["split_basis"] for row in rows} == {"voice_disjoint_synthetic"}
    assert all(row["clean_reference_available"] is True for row in rows)
    assert all(row["source_clip_id"] in row["id"] for row in rows)
    assert rows[0]["reference_provenance"] == "team script"
    output_audio = manifest_path.parent / rows[0]["audio_path"]
    info = sf.info(output_audio)
    assert info.samplerate == 8000 and info.channels == 1
    assert info.duration == pytest.approx(0.2, abs=0.01)


def test_team_audit_writes_measurements_without_inferencing_source(tmp_path):
    team_dir = tmp_path / "team"
    _write_team(team_dir)
    output = tmp_path / "manifests" / "team_audit.csv"
    rows, failures = audit_team_audio(team_dir, output)
    assert not failures
    assert len(rows) == 54
    assert all(row["likely_synthetic_advisory"] == "not_inferred" for row in rows)
    assert all(row["native_sample_rate_hz"] in {24000, 48000} for row in rows)
    with output.open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 54


def test_incomplete_review_reports_rows_and_sentence_split_is_explicit(tmp_path):
    team_dir = tmp_path / "team"
    _write_team(team_dir, complete=False, one_speaker=True)
    invalid = validate_team_data(team_dir)
    assert any("source_type" in message for _, message in invalid.issues)
    assert any("split is required" in message for _, message in invalid.issues)
    sentence_split = validate_team_data(team_dir, allow_sentence_split=True)
    assert sentence_split.split_basis == "sentence_disjoint_NOT_speaker_disjoint"
    assert not any("split is required" in message for _, message in sentence_split.issues)
    assert sentence_split.split_by_id["hi_01"] == sentence_split.split_by_id["kn_01"]
    assert sentence_split.split_by_id["hi_01"] == sentence_split.split_by_id["en_01"]
    assert sentence_split.split_by_id["hi_01"] != sentence_split.split_by_id["hi_02"]


def test_reused_synthetic_voice_must_stay_in_one_split_across_languages(tmp_path):
    team_dir = tmp_path / "team"
    _write_team(team_dir)
    review_path = team_dir / "voice_group_review.csv"
    with review_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
        fields = rows[0].keys()
    rows_by_id = {row["id"]: row for row in rows}
    rows_by_id["hi_01"]["speaker_id_confirmed"] = "spk_shared01"
    rows_by_id["en_02"]["speaker_id_confirmed"] = "spk_shared01"
    with review_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    validation = validate_team_data(team_dir)

    assert validation.split_basis == "voice_disjoint_synthetic"
    assert any("spk_shared01" in message and "multiple splits" in message
               for _, message in validation.issues)
