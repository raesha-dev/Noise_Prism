"""Convert eligible labeled audio to 8 kHz mono and split speakers without overlap."""
from __future__ import annotations

import argparse
import csv
import random
import re
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    return samples.mean(axis=1).astype(np.float32), int(sample_rate)


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "clip"


def _speaker_split(rows: list[dict[str, str]], test_fraction: float, seed: int) -> tuple[list, list]:
    speakers = sorted({row["speaker_id"] for row in rows})
    random.Random(seed).shuffle(speakers)
    count = round(len(speakers) * test_fraction)
    if len(speakers) > 1:
        count = min(len(speakers) - 1, max(1, count))
    else:
        count = 0
    test_speakers = set(speakers[:count])
    return [r for r in rows if r["speaker_id"] not in test_speakers], [r for r in rows if r["speaker_id"] in test_speakers]


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["id", "audio_path", "language", "reference", "speaker_id", "group"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--native-8khz-only", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.test_fraction < 1:
        parser.error("--test-fraction must be in [0, 1).")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = args.output_dir / "audio"
    audio_dir.mkdir(exist_ok=True)
    accepted: list[dict[str, str]] = []
    with args.input_csv.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            source = Path(row["audio_path"])
            if not source.is_absolute():
                source = args.input_csv.parent / source
            samples, sample_rate = _read_mono(source)
            duration = len(samples) / sample_rate
            if duration > 15.0 or duration <= 0:
                continue
            if args.native_8khz_only and sample_rate != 8000:
                continue
            if sample_rate != 8000:
                samples = signal.resample_poly(samples, 8000, sample_rate).astype(np.float32)
            clip_id = str(row["id"])
            filename = f"{_safe_id(clip_id)}.wav"
            sf.write(audio_dir / filename, np.clip(samples, -1, 1), 8000, subtype="PCM_16")
            accepted.append({
                "id": clip_id, "audio_path": f"audio/{filename}", "language": row["language"],
                "reference": row["reference"], "speaker_id": row["speaker_id"],
                "group": row.get("group", "") or "",
            })
    dev, test = _speaker_split(accepted, args.test_fraction, args.seed)
    _write_manifest(args.output_dir / "dev.csv", dev)
    _write_manifest(args.output_dir / "test.csv", test)


if __name__ == "__main__":
    main()
