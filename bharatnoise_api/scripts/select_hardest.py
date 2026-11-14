"""Select the lowest-quality C0 development clips using their human references."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

try:
    from ..metrics import score_utterance
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from metrics import score_utterance


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def select_hardest(
    manifest_path: Path, baseline_path: Path, output_path: Path, limit: int = 20,
) -> list[dict[str, str]]:
    if limit < 1:
        raise ValueError("limit must be positive.")
    manifest_path = manifest_path.resolve()
    manifest_fields, manifest_rows = _read(manifest_path)
    baseline_fields, baseline_rows = _read(baseline_path.resolve())
    if not {"id", "audio_path", "reference", "split"} <= set(manifest_fields):
        raise ValueError("Development manifest must contain id,audio_path,reference,split.")
    if not {"id", "reference", "hypothesis", "split"} <= set(baseline_fields):
        raise ValueError("C0 results must contain id,reference,hypothesis,split.")
    if any(row.get("split", "").lower() not in {"dev", "development"} for row in manifest_rows + baseline_rows):
        raise ValueError("Hard-clip selection is development-only.")
    by_id = {row["id"]: row for row in baseline_rows}
    ranked = []
    for row in manifest_rows:
        result = by_id.get(row["id"])
        if result is None:
            continue
        if result.get("candidate") and result["candidate"] != "C0":
            raise ValueError("Baseline results must be C0-only.")
        reference = row.get("reference", "")
        hypothesis = result.get("hypothesis", "")
        errors = bool(result.get("error")) or not hypothesis.strip()
        score = score_utterance(reference, hypothesis)
        wer = score.word.errors / score.word.n if score.word.n else 0.0
        ranked.append((errors, wer, row["id"], row, wer))
    if not ranked:
        raise ValueError("No shared C0 development clip IDs were found.")
    ranked.sort(key=lambda item: (not item[0], -item[1], item[2]))
    selected = []
    for rank, (failed, _, _, row, wer) in enumerate(ranked[:limit], 1):
        audio_path = Path(row["audio_path"])
        if not audio_path.is_absolute():
            audio_path = manifest_path.parent / audio_path
        if not audio_path.is_file():
            raise ValueError(f"Selected clip {row['id']!r} has a missing audio file.")
        selected.append({
            **row,
            "audio_path": str(audio_path.resolve()),
            "selection_order": str(rank),
            "c0_wer": f"{wer:.6f}",
            "c0_failed_or_empty": str(failed).lower(),
        })
    fields = [*manifest_fields, "selection_order", "c0_wer", "c0_failed_or_empty"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--c0-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    try:
        selected = select_hardest(args.manifest, args.c0_results, args.output, args.limit)
    except (OSError, ValueError) as exc:
        print(f"HARD-CLIP SELECTION ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Selected {len(selected)} hardest C0 development clips; no test rows were read.")
    print(f"Manifest: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
