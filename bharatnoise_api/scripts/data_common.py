"""Shared manifest and provenance helpers for local-only corpus preparation."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Iterable

import soundfile as sf

MANIFEST_FIELDS = [
    "id",
    "source_clip_id",
    "audio_path",
    "language",
    "reference",
    "speaker_id",
    "group",
    "source",
    "source_type",
    "base_source_type",
    "license",
    "reference_provenance",
    "native_sample_rate",
    "split",
    "split_basis",
    "clean_reference_available",
    "noise_type",
    "snr_db_nominal",
    "snr_db_measured",
    "snr_basis",
]
DERIVED_MANIFESTS = {
    "clean_control_dev.csv",
    "clean_control_test.csv",
    "gramvaani_eval.csv",
    "gramvaani_dev.csv",
    "gramvaani_dev_sample60.csv",
    "gramvaani_probe10.csv",
    "hardest20_dev.csv",
}


def truthy(value: object) -> bool:
    """Parse common explicit true values; missing/unknown is false."""
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def source_manifest_paths(manifest_dir: Path) -> list[Path]:
    """Return source manifests only, excluding audits and working selections."""
    return sorted(
        path for path in manifest_dir.glob("*.csv")
        if not path.name.endswith("_audit.csv")
        and path.name != "team_audit.csv"
        and path.name not in DERIVED_MANIFESTS
    )


def write_manifest(path: Path, rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def audio_summary(rows: list[dict[str, str]]) -> tuple[dict[str, Counter], float]:
    groups = {
        key: Counter(row.get(key, "") or "(blank)" for row in rows)
        for key in ("source", "source_type", "language", "split", "speaker_id")
    }
    minutes = 0.0
    for row in rows:
        path = Path(row["audio_path"])
        if not path.is_absolute():
            path = Path(row.get("_manifest_dir", ".")) / path
        minutes += sf.info(path).duration / 60
    return groups, minutes


def write_provenance(path: Path, manifest_paths: list[Path]) -> None:
    """Write aggregate-only dataset provenance, never clip IDs or transcript text."""
    lines = [
        "# Local data provenance",
        "",
        "Generated from local manifests. No audio, transcript, file path, or clip identifier is included.",
        "",
        "| Source | Source type | Language | Split | Clips | Independent source clips | Speakers | Audio minutes | License |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for manifest_path in manifest_paths:
        rows = read_rows(manifest_path)
        for source in sorted({row.get("source", "") for row in rows}):
            for source_type in sorted({
                row.get("source_type", "") for row in rows if row.get("source", "") == source
            }):
                for language in sorted({
                    row.get("language", "") for row in rows
                    if row.get("source", "") == source and row.get("source_type", "") == source_type
                }):
                    for split in sorted({
                        row.get("split", "") for row in rows
                        if row.get("source", "") == source
                        and row.get("source_type", "") == source_type
                        and row.get("language", "") == language
                    }):
                        selected = [
                            row for row in rows
                            if row.get("source", "") == source
                            and row.get("source_type", "") == source_type
                            and row.get("language", "") == language
                            and row.get("split", "") == split
                        ]
                        speakers = {row.get("speaker_id", "") for row in selected if row.get("speaker_id", "")}
                        source_clips = {
                            row.get("source_clip_id") or row.get("id", "")
                            for row in selected
                            if row.get("source_clip_id") or row.get("id", "")
                        }
                        minutes = 0.0
                        for row in selected:
                            audio_path = Path(row["audio_path"])
                            if not audio_path.is_absolute():
                                audio_path = manifest_path.parent / audio_path
                            minutes += sf.info(audio_path).duration / 60
                        licenses = sorted({row.get("license", "") for row in selected if row.get("license", "")})
                        lines.append(
                            f"| {source or '(unspecified)'} | {source_type or '(unspecified)'} | "
                            f"{language or '(unspecified)'} | {split or '(unspecified)'} | "
                            f"{len(selected)} | {len(source_clips)} | {len(speakers)} | "
                            f"{minutes:.3f} | {', '.join(licenses) or '(unspecified)'} |"
                        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
