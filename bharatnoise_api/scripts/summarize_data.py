"""Print aggregate-only manifest counts, independent clips, minutes, and provenance."""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import soundfile as sf

try:
    from .data_common import read_rows, source_manifest_paths, write_provenance
except ImportError:
    from data_common import read_rows, source_manifest_paths, write_provenance

ROOT = Path(__file__).resolve().parents[1]


def _manifests(manifest_dir: Path) -> list[Path]:
    result = []
    for path in source_manifest_paths(manifest_dir):
        with path.open(newline="", encoding="utf-8-sig") as handle:
            if {"id", "audio_path"} <= set(csv.DictReader(handle).fieldnames or []):
                result.append(path)
    return result


def summarize(data_dir: Path) -> dict[str, object]:
    manifest_paths = _manifests(data_dir / "manifests")
    dimensions: dict[str, Counter[str]] = {
        key: Counter() for key in ("source", "source_type", "language", "split", "speaker_id")
    }
    source_clip_counts: Counter[tuple[str, str]] = Counter()
    minutes_by_split: Counter[str] = Counter()
    total_minutes = 0.0
    total_rows = 0
    for manifest_path in manifest_paths:
        rows = read_rows(manifest_path)
        total_rows += len(rows)
        for row in rows:
            for key, counts in dimensions.items():
                counts[row.get(key, "").strip() or "(blank)"] += 1
            split = row.get("split", "").strip() or "(blank)"
            audio_path = Path(row["audio_path"])
            if not audio_path.is_absolute():
                audio_path = manifest_path.parent / audio_path
            duration = float(sf.info(audio_path).duration)
            total_minutes += duration / 60
            minutes_by_split[split] += duration / 60
            source_clip_id = row.get("source_clip_id", "").strip()
            if source_clip_id:
                source_clip_counts[(split, source_clip_id)] += 1
    write_provenance(data_dir / "PROVENANCE.md", manifest_paths)
    noise_licenses = data_dir / "noise" / "LICENSES.csv"
    noise_counts: Counter[str] = Counter()
    noise_minutes = 0.0
    if noise_licenses.is_file():
        with noise_licenses.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                path = data_dir / "noise" / row["file"]
                noise_minutes += float(sf.info(path).duration) / 60
                noise_counts[row.get("type", "").strip() or "(unspecified)"] += 1
    return {
        "manifests": manifest_paths,
        "dimensions": dimensions,
        "independent_source_clips": Counter(split for split, _ in source_clip_counts),
        "minutes_by_split": minutes_by_split,
        "total_minutes": total_minutes,
        "total_rows": total_rows,
        "noise_counts": noise_counts,
        "noise_minutes": noise_minutes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    try:
        summary = summarize(args.data_dir)
    except (OSError, RuntimeError, ValueError, csv.Error) as exc:
        print(f"DATA SUMMARY ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Manifest files: {len(summary['manifests'])}; manifest rows: {summary['total_rows']}")
    for key, counts in summary["dimensions"].items():
        print(f"{key}: {dict(sorted(counts.items()))}")
    print(f"Independent source clips by split: {dict(sorted(summary['independent_source_clips'].items()))}")
    print(f"Manifest audio minutes by split: {dict(sorted(summary['minutes_by_split'].items()))}")
    print(f"Manifest audio minutes: {summary['total_minutes']:.3f}")
    print(f"Noise segments by type: {dict(sorted(summary['noise_counts'].items()))}")
    print(f"Noise audio minutes: {summary['noise_minutes']:.3f}")
    print(f"Aggregate provenance: {args.data_dir / 'PROVENANCE.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
