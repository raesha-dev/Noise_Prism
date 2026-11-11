"""Validate a manifest and report planned calls and synthetic/recorded audio minutes."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
VALID_CANDIDATES = ("C0", "C1", "C2", "C3")
VALID_MODELS = ("v2.5", "v3.0")


def project_path(path: Path) -> Path:
    """Resolve relative user paths against the package directory, not process cwd."""
    return path if path.is_absolute() else ROOT / path


def manifest_durations(manifest: Path) -> list[float]:
    path = project_path(manifest).resolve()
    if not path.is_file():
        raise ValueError(f"Manifest does not exist: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "audio_path" not in reader.fieldnames:
            raise ValueError(f"Manifest must contain an audio_path column: {path}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"Manifest contains no clips: {path}")
    durations = []
    for row_number, row in enumerate(rows, start=2):
        source = Path(row["audio_path"])
        if not source.is_absolute():
            source = path.parent / source
        try:
            duration = float(sf.info(source).duration)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(f"Could not inspect manifest row {row_number} audio ({type(exc).__name__}).") from None
        if duration <= 0 or duration > 30:
            raise ValueError(f"Manifest row {row_number} has invalid clip length ({duration:.3f}s); maximum is 30s.")
        durations.append(duration)
    return durations


def plan_manifest(
    manifest: Path,
    candidates: list[str],
    models: list[str],
    runs: int,
) -> tuple[int, float]:
    if not candidates or set(candidates) - set(VALID_CANDIDATES):
        raise ValueError(f"candidates must be selected from {', '.join(VALID_CANDIDATES)}.")
    if not models or set(models) - set(VALID_MODELS):
        raise ValueError(f"models must be selected from {', '.join(VALID_MODELS)}.")
    if runs < 1:
        raise ValueError("runs must be at least one.")
    durations = manifest_durations(manifest)
    seconds = sum(durations) * len(set(candidates)) * len(set(models)) * runs
    calls = len(durations) * len(set(candidates)) * len(set(models)) * runs
    return calls, seconds / 60


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Manifest CSV with audio_path")
    parser.add_argument("--candidates", nargs="+", choices=VALID_CANDIDATES, default=["C0"])
    parser.add_argument("--models", nargs="+", choices=VALID_MODELS, default=["v2.5"])
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    try:
        durations = manifest_durations(args.manifest)
        calls = len(durations) * len(set(args.candidates)) * len(set(args.models)) * args.runs
        minutes = sum(durations) * len(set(args.candidates)) * len(set(args.models)) * args.runs / 60
        if args.runs < 1:
            raise ValueError("runs must be at least one.")
    except ValueError as exc:
        print(f"PLAN ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Manifest clips: {len(durations)}")
    print(f"Candidates: {', '.join(dict.fromkeys(args.candidates))}")
    print(f"Models: {', '.join(dict.fromkeys(args.models))}")
    print(f"Runs per combination: {args.runs}")
    print(f"Planned API calls: {calls}")
    print(f"Planned audio: {minutes:.6f} minutes")
    print("No API requests were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
