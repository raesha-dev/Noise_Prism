import csv
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from .scripts import import_gramvaani


def _write_corpus(root: Path) -> None:
    for folder, ids in (("GV_Dev_5h", ("dev-a", "dev-b")), ("GV_Eval_3h", ("test-a",))):
        split = root / folder
        audio = split / "Audio"
        audio.mkdir(parents=True)
        (split / "text").write_text(
            "".join(f"{clip_id} \u0928\u092e\u0942\u0928\u093e {clip_id}\n" for clip_id in ids),
            encoding="utf-8",
        )
        for clip_id in ids:
            (audio / f"{clip_id}.mp3").touch()


def test_gramvaani_import_keeps_split_and_audits_dev_only(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _write_corpus(source)
    audit = tmp_path / "manifests" / "audit.csv"
    audit.parent.mkdir()
    with audit.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "audio_path", "split", "reference", "reference_ok"])
        writer.writeheader()
        writer.writerow({
            "id": "gramvaani-dev-dev-a",
            "audio_path": "",
            "split": "dev",
            "reference": "",
            "reference_ok": "0",
        })

    def fake_info(path):
        return SimpleNamespace(
            samplerate=8000,
            duration=3.0,
        )

    monkeypatch.setattr(import_gramvaani.sf, "info", fake_info)
    monkeypatch.setattr(
        import_gramvaani,
        "_decode",
        lambda _path: (np.zeros(24000, dtype=np.float32), 8000),
    )
    manifest = tmp_path / "manifests" / "gramvaani.csv"
    output_audio = tmp_path / "audio" / "gramvaani"
    rows, audit_rows, skipped = import_gramvaani.import_corpus(
        source, output_audio, manifest, audit, seed=7, audit_size=30
    )

    assert {row["split"] for row in rows} == {"dev", "test"}
    assert all(not row["speaker_id"] for row in rows)
    assert all(row["license"] == "Free for academic use; no redistribution" for row in rows)
    assert all(row["source_type"] == "human_recording" for row in rows)
    assert all(row["source_clip_id"] for row in rows)
    assert all(row["reference_provenance"] == "Organizer-provided text file" for row in rows)
    assert len(audit_rows) == 1
    assert audit_rows[0]["split"] == "dev"
    assert audit_rows[0]["reference_ok"] == ""
    assert skipped["manually_rejected_reference"] == 1
    assert (manifest.parent / rows[0]["audio_path"]).is_file()
    assert (manifest.parent.parent / "PROVENANCE.md").is_file()


def test_gramvaani_audit_exclusions_accept_prefixed_ids(tmp_path):
    audit = tmp_path / "audit.csv"
    audit.write_text(
        "id,audio_path,split,reference,reference_ok\n"
        "gramvaani-dev-clip-1,,dev,,0\n",
        encoding="utf-8",
    )
    assert import_gramvaani._read_audit_exclusions(audit) == {("dev", "clip-1")}
