"""Measure team WAV metadata and acoustic advisories without assigning labels."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

try:
    from .team_data import nfc, read_csv
except ImportError:
    from team_data import nfc, read_csv

ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "id", "filename", "native_sample_rate_hz", "duration_s", "noise_floor_db",
    "noise_floor_censored", "digital_silence_frac", "approx_pitch_hz",
    "likely_synthetic_advisory", "advisory_flags",
]


def _pitch_estimate(samples: np.ndarray, rate: int) -> float | None:
    frame_size = max(1, round(0.04 * rate))
    hop = max(1, round(0.02 * rate))
    low_lag = max(1, rate // 400)
    high_lag = min(frame_size - 1, rate // 70)
    pitches: list[float] = []
    for start in range(0, max(0, len(samples) - frame_size + 1), hop):
        frame = samples[start:start + frame_size].astype(np.float64)
        frame -= frame.mean()
        if np.sqrt(np.mean(frame**2)) < 1e-5:
            continue
        correlation = np.correlate(frame, frame, mode="full")[frame_size - 1:]
        if correlation[0] <= 0 or high_lag <= low_lag:
            continue
        lag = low_lag + int(np.argmax(correlation[low_lag:high_lag + 1]))
        if correlation[lag] / correlation[0] >= 0.3:
            pitches.append(rate / lag)
    return float(np.median(pitches)) if pitches else None


def audit_file(path: Path, clip_id: str = "") -> dict[str, object]:
    """Return descriptive measurements; acoustic clues never decide source type."""
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = samples.mean(axis=1).astype(np.float32)
    exact_zero_fraction = float(np.mean(samples == 0)) if samples.size else 0.0
    frame_size = max(1, round(0.02 * rate))
    usable = len(mono) - len(mono) % frame_size
    frames = mono[:usable].reshape(-1, frame_size) if usable else np.zeros((0, frame_size))
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)) if len(frames) else np.array([])
    floor_rms = float(np.percentile(rms, 20)) if len(rms) else 0.0
    censored = floor_rms == 0.0
    floor_db = "" if censored else 20.0 * float(np.log10(floor_rms))
    flags = ["exact_zero_samples_present"] if exact_zero_fraction > 0 else []
    if censored:
        flags.append("noise_floor_censored_by_digital_silence")
    return {
        "id": clip_id,
        "filename": path.name,
        "native_sample_rate_hz": int(rate),
        "duration_s": len(mono) / rate,
        "noise_floor_db": floor_db,
        "noise_floor_censored": censored,
        "digital_silence_frac": exact_zero_fraction,
        "approx_pitch_hz": _pitch_estimate(mono, int(rate)),
        "likely_synthetic_advisory": "not_inferred",
        "advisory_flags": ";".join(flags),
    }


def audit_team_audio(team_dir: Path, output_path: Path) -> tuple[list[dict[str, object]], list[str]]:
    script_rows = read_csv(team_dir / "script_with_voice_filenames.csv")
    id_by_filename = {nfc(row.get("filename", "")): row.get("id", "") for row in script_rows}
    audio_dir = team_dir / "audio"
    if not audio_dir.is_dir():
        raise FileNotFoundError(f"Team audio directory does not exist: {audio_dir}")
    rows: list[dict[str, object]] = []
    failures: list[str] = []
    for path in sorted(audio_dir.iterdir(), key=lambda item: nfc(item.name)):
        if not path.is_file() or path.suffix.lower() != ".wav":
            continue
        try:
            rows.append(audit_file(path, id_by_filename.get(nfc(path.name), "")))
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-dir", type=Path, default=ROOT / "data" / "team")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "manifests" / "team_audit.csv")
    args = parser.parse_args()
    try:
        rows, failures = audit_team_audio(args.team_dir, args.output)
    except (OSError, csv.Error) as exc:
        print(f"TEAM AUDIO AUDIT ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Audited audio files: {len(rows)}")
    print(f"Wrote advisory measurements: {args.output}")
    print("Source type is not inferred; likely_synthetic_advisory is not_inferred.")
    if failures:
        for failure in failures:
            print(f"DECODE ERROR: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
