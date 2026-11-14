import csv
from pathlib import Path

import numpy as np
import soundfile as sf

from .scripts.import_noise import import_noise


def test_noise_import_is_8khz_segmented_and_time_disjoint(tmp_path):
    demand = tmp_path / "raw" / "demand"
    scene = demand / "STRAFFIC_16k" / "STRAFFIC"
    scene.mkdir(parents=True)
    samples = np.zeros((25 * 16000, 2), dtype=np.float32)
    samples[:, 0] = np.arange(len(samples), dtype=np.float32) / len(samples) * 0.4
    samples[:, 1] = samples[:, 0] * 0.5
    sf.write(scene / "ch01.wav", samples, 16000, subtype="PCM_16")

    licenses, counts, missing, errors = import_noise(
        demand, tmp_path / "team-noise", tmp_path / "noise"
    )
    assert not errors
    assert missing == ["cafeteria", "station", "bus", "living"]
    assert counts == {"traffic": 2}
    assert len(licenses) == 2
    assert {row["file"].split("/")[1] for row in licenses} == {"dev", "test"}
    assert all(row["license"].startswith("CC BY-SA 3.0") for row in licenses)
    assert all("do not redistribute" in row["note"] for row in licenses)
    for row in licenses:
        info = sf.info(tmp_path / "noise" / row["file"])
        assert info.samplerate == 8000 and info.channels == 1
        assert info.duration == 10.0
    dev, _ = sf.read(tmp_path / "noise" / licenses[0]["file"], dtype="float32")
    test, _ = sf.read(tmp_path / "noise" / licenses[1]["file"], dtype="float32")
    assert not np.array_equal(dev, test)
    with (tmp_path / "noise" / "LICENSES.csv").open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 2
