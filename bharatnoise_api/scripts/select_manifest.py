"""Select explicitly labeled manifest rows without inferring dataset metadata."""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

try:
    from .data_common import truthy
except ImportError:
    from data_common import truthy


def select(
    source_path: Path, output_path: Path, split: str | None = None,
    group: str | None = None, clean_reference_only: bool = False,
) -> list[dict[str, str]]:
    source_path = source_path.resolve()
    with source_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"id", "audio_path"} <= set(reader.fieldnames):
            raise ValueError("Manifest must contain id and audio_path columns.")
        fields = list(reader.fieldnames)
        rows = list(reader)
    selected = [
        row for row in rows
        if (split is None or row.get("split", "").strip().lower() == split.lower())
        and (group is None or row.get("group", "").strip() == group)
        and (not clean_reference_only or truthy(row.get("clean_reference_available", "")))
    ]
    if not selected:
        raise ValueError("No rows match the explicit split/group/reference filters.")
    for row in selected:
        audio_path = Path(row["audio_path"])
        if not audio_path.is_absolute():
            audio_path = source_path.parent / audio_path
        audio_path = audio_path.resolve()
        if not audio_path.is_file():
            raise ValueError(f"Selected row {row['id']!r} has a missing audio file.")
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
    parser.add_argument("--split")
    parser.add_argument("--group")
    parser.add_argument("--clean-reference-only", action="store_true")
    args = parser.parse_args()
    try:
        rows = select(args.manifest, args.output, args.split, args.group, args.clean_reference_only)
    except (OSError, ValueError) as exc:
        print(f"MANIFEST SELECTION ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Selected {len(rows)} explicitly labeled rows.")
    for field in ("split", "language", "group", "source_type"):
        counts = Counter(row.get(field, "").strip() or "(blank)" for row in rows)
        print(f"{field}: {dict(sorted(counts.items()))}")
    print(f"Manifest: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
