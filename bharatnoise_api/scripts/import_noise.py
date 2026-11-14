"""Prepare licensed, non-overlapping 8 kHz noise segments for evaluation."""
from __future__ import annotations

import argparse
import csv
import sys
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]
DEMAND_TYPES = {
    "STRAFFIC": "traffic",
    "PCAFETER": "cafeteria",
    "PSTATION": "station",
    "TBUS": "bus",
    "DLIVING": "living",
}
SEGMENT_SECONDS = 10
LICENSE_FIELDS = ["file", "type", "source", "license", "note"]


def _read_mono_8k(path: Path) -> np.ndarray:
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    if samples.size == 0:
        raise ValueError(f"{path.name} is empty.")
    if samples.shape[1] > 2:
        raise ValueError(f"{path.name} has {samples.shape[1]} channels; at most two are accepted.")
    mono = samples.mean(axis=1).astype(np.float32)
    if int(rate) != 8000:
        divisor = gcd(int(rate), 8000)
        mono = signal.resample_poly(mono, 8000 // divisor, int(rate) // divisor).astype(np.float32)
    return mono


def _scene_segments(samples: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Split each recording into disjoint 10-second dev/test time regions."""
    split_sample = int(len(samples) * 0.6)
    ten_seconds = 10 * 8000
    result: list[tuple[str, np.ndarray]] = []
    for split, start, end in (
        ("dev", 0, split_sample),
        ("test", split_sample, len(samples)),
    ):
        segment_number = 0
        for offset in range(start, end - ten_seconds + 1, ten_seconds):
            result.append((split, samples[offset:offset + ten_seconds]))
            segment_number += 1
        if end - start >= ten_seconds and segment_number == 0:
            raise RuntimeError("Noise segment partitioning failed unexpectedly.")
    return result


def _demand_recordings(raw_demand: Path) -> tuple[dict[str, list[Path]], list[str]]:
    recordings = {noise_type: [] for noise_type in DEMAND_TYPES.values()}
    missing: list[str] = []
    for folder_prefix, noise_type in DEMAND_TYPES.items():
        folders = sorted(path for path in raw_demand.glob(f"{folder_prefix}*") if path.is_dir())
        if not folders:
            missing.append(noise_type)
            continue
        for folder in folders:
            recordings[noise_type].extend(sorted(folder.rglob("*.wav")))
    return recordings, missing


def import_noise(
    raw_demand: Path, team_noise: Path, output_dir: Path
) -> tuple[list[dict[str, str]], dict[str, int], list[str], list[str]]:
    demand, missing = _demand_recordings(raw_demand)
    sources: list[tuple[Path, str, str, str, str]] = []
    for noise_type, paths in demand.items():
        sources.extend(
            (path, noise_type, "DEMAND", "CC BY-SA 3.0 (conservative license assumption)",
             "Attribute DEMAND; do not redistribute source noise or mixtures.")
            for path in paths
        )
    team_paths = sorted(team_noise.glob("*.wav")) if team_noise.is_dir() else []
    sources.extend(
        (path, "team_noise", "team", "Unverified; no redistribution",
         "Team-provided source; verify permission before use.")
        for path in team_paths
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    licenses: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    errors: list[str] = []
    for recording_number, (source_path, noise_type, source, license_name, note) in enumerate(sources):
        try:
            samples = _read_mono_8k(source_path)
            segments = _scene_segments(samples)
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"{source_path.name}: {type(exc).__name__}: {exc}")
            continue
        per_source_index: dict[str, int] = {}
        for split, segment in segments:
            index = per_source_index.get(split, 0)
            per_source_index[split] = index + 1
            relative = Path(noise_type) / split / f"scene{recording_number:04d}_{index:03d}.wav"
            target = output_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            sf.write(target, segment, 8000, subtype="PCM_16")
            licenses.append(
                {
                    "file": relative.as_posix(),
                    "type": noise_type,
                    "source": source,
                    "license": license_name,
                    "note": note,
                }
            )
            counts[noise_type] = counts.get(noise_type, 0) + 1
    with (output_dir / "LICENSES.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LICENSE_FIELDS)
        writer.writeheader()
        writer.writerows(licenses)
    return licenses, counts, missing, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-demand", type=Path, default=ROOT / "data" / "raw" / "demand")
    parser.add_argument("--team-noise", type=Path, default=ROOT / "data" / "team" / "noise")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "noise")
    args = parser.parse_args()
    try:
        licenses, counts, missing, errors = import_noise(
            args.raw_demand, args.team_noise, args.output_dir
        )
    except (OSError, csv.Error) as exc:
        print(f"NOISE IMPORT ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Noise types available: {', '.join(sorted(counts)) or '(none)'}")
    if missing:
        print(f"Missing DEMAND types skipped: {', '.join(missing)}")
    if len(counts) < 2:
        print("WARNING: fewer than two noise types are available.")
    print(f"Segments written: {len(licenses)}; counts by type: {counts}")
    print(f"License manifest: {args.output_dir / 'LICENSES.csv'}")
    for error in errors:
        print(f"SKIPPED: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
