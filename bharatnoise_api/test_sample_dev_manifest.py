import csv

import pytest

from .scripts.sample_dev_manifest import sample_dev_manifest


def _write_manifest(path, splits):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "audio_path", "split"])
        writer.writeheader()
        for index, split in enumerate(splits):
            writer.writerow({
                "id": f"clip-{index}",
                "audio_path": f"audio-{index}.wav",
                "split": split,
            })
            (path.parent / f"audio-{index}.wav").write_bytes(b"fixture")


def test_dev_sample_is_reproducible_and_keeps_source_rows(tmp_path):
    manifest = tmp_path / "source.csv"
    _write_manifest(manifest, ["dev"] * 8)

    first = sample_dev_manifest(manifest, tmp_path / "first.csv", limit=4, seed=17)
    second = sample_dev_manifest(manifest, tmp_path / "second.csv", limit=4, seed=17)

    assert [row["id"] for row in first] == [row["id"] for row in second]
    assert len({row["id"] for row in first}) == 4
    assert all(row["split"] == "dev" for row in first)


def test_dev_sample_rejects_mixed_split_input(tmp_path):
    manifest = tmp_path / "source.csv"
    _write_manifest(manifest, ["dev", "test"])

    with pytest.raises(ValueError, match="development rows only"):
        sample_dev_manifest(manifest, tmp_path / "sample.csv", limit=1, seed=0)
