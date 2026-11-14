"""Import eligible local GramVaani clips without publishing source data."""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

try:
    from ..metrics import normalize
    from .data_common import source_manifest_paths, write_manifest, write_provenance
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from metrics import normalize
    from scripts.data_common import source_manifest_paths, write_manifest, write_provenance

ROOT = Path(__file__).resolve().parents[1]
SPLITS = {"GV_Dev_5h": "dev", "GV_Eval_3h": "test"}


def _read_id_text(path: Path, *, has_header: bool = False) -> dict[str, str]:
    records: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", errors="replace") as handle:
        for line_number, line in enumerate(handle):
            fields = line.rstrip("\r\n").split(maxsplit=1)
            if len(fields) != 2 or (has_header and line_number == 0):
                continue
            records[fields[0]] = fields[1]
    return records


def _decode(path: Path) -> tuple[np.ndarray, int]:
    try:
        samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    except (OSError, RuntimeError, ValueError) as exc:
        message = (
            f"soundfile could not decode {path.name}: {type(exc).__name__}: {exc}. "
            "Install a libsndfile build with MP3 support (for example, "
            "`brew install libsndfile` on macOS, or `conda install -c conda-forge libsndfile`), "
            "then retry. The importer does not invoke ffmpeg."
        )
        raise RuntimeError(message) from exc
    if samples.size == 0:
        raise RuntimeError(f"soundfile decoded {path.name} to an empty signal.")
    return samples.mean(axis=1).astype(np.float32), int(sample_rate)


def _read_audit_exclusions(path: Path) -> set[tuple[str, str]]:
    if not path.is_file():
        return set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        exclusions = set()
        for row in csv.DictReader(handle):
            split, clip_id = row.get("split", ""), row.get("id", "")
            clip_id = clip_id.removeprefix(f"gramvaani-{split}-")
            if row.get("reference_ok", "").strip() == "0":
                exclusions.add((split, clip_id))
        return exclusions


def import_corpus(
    source_root: Path,
    output_audio: Path,
    manifest_path: Path,
    audit_path: Path,
    *,
    seed: int = 0,
    audit_size: int = 30,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, int]]:
    """Convert native-8-kHz 2–15-second clips and preserve the organizer split."""
    excluded = _read_audit_exclusions(audit_path)
    rows: list[dict[str, object]] = []
    skipped: dict[str, int] = {}
    decode_error: RuntimeError | None = None

    for folder_name, split in SPLITS.items():
        folder = source_root / folder_name
        audio_dir = folder / "Audio"
        transcript_path = folder / "text"
        if not audio_dir.is_dir() or not transcript_path.is_file():
            raise FileNotFoundError(
                f"Expected {folder_name}/Audio/*.mp3 and {folder_name}/text under {source_root}."
            )
        transcripts = _read_id_text(transcript_path)
        audio_files = sorted(audio_dir.glob("*.mp3"))
        for source_audio in audio_files:
            clip_id = source_audio.stem
            reference = transcripts.get(clip_id, "")
            try:
                info = sf.info(source_audio)
            except (OSError, RuntimeError, ValueError) as exc:
                raise RuntimeError(
                    f"soundfile could not inspect {source_audio.name}: {type(exc).__name__}: {exc}"
                ) from exc
            if info.samplerate != 8000:
                skipped["not_native_8khz"] = skipped.get("not_native_8khz", 0) + 1
                continue
            if not 2.0 <= info.duration <= 15.0:
                skipped["duration_outside_2_to_15_s"] = skipped.get("duration_outside_2_to_15_s", 0) + 1
                continue
            if not reference or not normalize(reference):
                skipped["missing_or_empty_reference"] = skipped.get("missing_or_empty_reference", 0) + 1
                continue
            if (split, clip_id) in excluded:
                skipped["manually_rejected_reference"] = skipped.get("manually_rejected_reference", 0) + 1
                continue
            try:
                mono, native_rate = _decode(source_audio)
            except RuntimeError as exc:
                decode_error = exc
                break
            target_audio = output_audio / split / f"{clip_id}.wav"
            relative_audio = Path(os.path.relpath(target_audio, manifest_path.parent))
            target_audio.parent.mkdir(parents=True, exist_ok=True)
            sf.write(target_audio, mono, native_rate, subtype="PCM_16")
            rows.append(
                {
                    "id": f"gramvaani-{split}-{clip_id}",
                    "source_clip_id": clip_id,
                    "audio_path": relative_audio.as_posix(),
                    "language": "hi-IN",
                    "reference": reference,
                    "speaker_id": "",
                    "group": "gramvaani_real_telephone",
                    "source": "gramvaani",
                    "source_type": "human_recording",
                    "license": "Free for academic use; no redistribution",
                    "reference_provenance": "Organizer-provided text file",
                    "native_sample_rate": native_rate,
                    "split": split,
                    "split_basis": "Organizer-provided GV_Dev_5h/GV_Eval_3h split",
                    "clean_reference_available": False,
                    "noise_type": "",
                    "snr_db_nominal": "",
                    "snr_db_measured": "",
                    "snr_basis": "",
                }
            )
        if decode_error is not None:
            break
    if decode_error is not None:
        raise decode_error

    existing_audit: dict[tuple[str, str], str] = {}
    if audit_path.is_file():
        with audit_path.open(newline="", encoding="utf-8-sig") as handle:
            existing_audit = {
                (row.get("split", ""), row.get("id", "")): row.get("reference_ok", "")
                for row in csv.DictReader(handle)
            }
    rng = random.Random(seed)
    dev_rows = [row for row in rows if row["split"] == "dev"]
    audit_rows: list[dict[str, object]] = []
    for row in rng.sample(dev_rows, min(audit_size, len(dev_rows))):
        key = (str(row["split"]), str(row["id"]).removeprefix(f"gramvaani-{row['split']}-"))
        audit_rows.append(
            {
                "id": row["id"],
                "audio_path": row["audio_path"],
                "split": row["split"],
                "reference": row["reference"],
                "reference_ok": existing_audit.get(key, ""),
            }
        )
    write_manifest(manifest_path, rows)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "audio_path", "split", "reference", "reference_ok"])
        writer.writeheader()
        writer.writerows(audit_rows)
    manifests = source_manifest_paths(manifest_path.parent)
    write_provenance(manifest_path.parent.parent / "PROVENANCE.md", manifests)
    return rows, audit_rows, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT / "data" / "raw" / "gramvaani")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data" / "manifests" / "gramvaani.csv")
    parser.add_argument("--audit", type=Path, default=ROOT / "data" / "manifests" / "gramvaani_audit.csv")
    parser.add_argument("--audio-dir", type=Path, default=ROOT / "data" / "audio" / "gramvaani")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--audit-size", type=int, default=30)
    args = parser.parse_args()
    try:
        rows, audit_rows, skipped = import_corpus(
            args.source_root, args.audio_dir.resolve(),
            args.manifest.resolve(), args.audit.resolve(), seed=args.seed, audit_size=args.audit_size,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"GRAMVAANI IMPORT ERROR: {exc}", file=sys.stderr)
        return 2
    split_counts = {split: sum(row["split"] == split for row in rows) for split in ("dev", "test")}
    minutes = 0.0
    for row in rows:
        minutes += sf.info(args.manifest.resolve().parent / str(row["audio_path"])).duration / 60
    print(f"Imported clips: {len(rows)} (dev={split_counts['dev']}, test={split_counts['test']})")
    print(f"Audit sample: {len(audit_rows)} clips; reference_ok is blank unless previously reviewed.")
    print(f"Converted audio duration: {minutes:.3f} minutes.")
    print(f"Skipped counts: {skipped}")
    if any(not row["speaker_id"] for row in rows):
        print("WARNING: no speaker IDs were present in the inspected metadata; speaker_id is blank.")
    print("Source audio/transcripts remain under ignored data/; do not copy them into samples/ or the UI.")
    print(f"Manifest: {args.manifest.resolve()}")
    print(f"Audit CSV: {args.audit.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
