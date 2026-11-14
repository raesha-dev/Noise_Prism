"""Write a reproducible random sample from an explicitly labeled dev manifest."""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path


def sample_dev_manifest(
    manifest_path: Path, output_path: Path, *, limit: int, seed: int,
) -> list[dict[str, str]]:
    """Sample only dev rows, preventing accidental access to held-out rows."""
    manifest_path = manifest_path.resolve()
    if limit < 1:
        raise ValueError("limit must be at least one.")
    with manifest_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if not {"id", "audio_path", "split"} <= set(fields):
            raise ValueError("Manifest must contain id, audio_path, and split columns.")
        rows = list(reader)
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Manifest contains duplicate clip IDs.")
    if any(row.get("split", "").strip().lower() not in {"dev", "development"} for row in rows):
        raise ValueError("Input manifest must contain development rows only.")
    if limit > len(rows):
        raise ValueError(f"Requested {limit} clips but only {len(rows)} development rows exist.")
    selected = random.Random(seed).sample(rows, limit)
    for row in selected:
        audio_path = Path(row["audio_path"])
        if not audio_path.is_absolute():
            audio_path = manifest_path.parent / audio_path
        audio_path = audio_path.resolve()
        if not audio_path.is_file():
            raise ValueError(f"Selected clip {row['id']!r} has a missing audio file.")
        row["audio_path"] = str(audio_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    try:
        selected = sample_dev_manifest(
            args.manifest, args.output, limit=args.limit, seed=args.seed
        )
    except (OSError, ValueError, csv.Error) as exc:
        print(f"DEV SAMPLE ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Sampled {len(selected)} development clips with seed {args.seed}.")
    print("Held-out rows were not read.")
    print(f"Manifest: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
