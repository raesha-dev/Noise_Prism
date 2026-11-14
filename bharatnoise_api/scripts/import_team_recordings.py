"""Import human-reviewed team audio as 8 kHz mono clean-control candidates."""
from __future__ import annotations

import argparse
import csv
import os
import sys
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

try:
    from .data_common import source_manifest_paths, write_manifest, write_provenance
    from .team_data import nfc, review_path, validate_team_data
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.data_common import source_manifest_paths, write_manifest, write_provenance
    from scripts.team_data import nfc, review_path, validate_team_data

ROOT = Path(__file__).resolve().parents[1]


def _read_clip(path: Path) -> tuple[np.ndarray, int]:
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if samples.size == 0:
        raise ValueError(f"{path.name} decoded to an empty signal.")
    return samples.mean(axis=1).astype(np.float32), int(sample_rate)


def import_team(
    team_dir: Path,
    audio_output: Path,
    manifest_path: Path,
    review_file: Path | None = None,
    *,
    allow_sentence_split: bool = False,
) -> list[dict[str, object]]:
    review_file = review_path(team_dir, review_file)
    validation = validate_team_data(
        team_dir, review_file, allow_sentence_split=allow_sentence_split
    )
    blockers = [
        (row_id, message)
        for row_id, message in validation.issues
        if "UNDERPOWERED:" not in message
    ]
    if blockers:
        details = "; ".join(f"{row_id}: {message}" if row_id else message for row_id, message in blockers)
        raise ValueError(f"Team review is incomplete or invalid: {details}")

    review_by_id = {row["id"]: row for row in validation.review_rows}
    rows: list[dict[str, object]] = []
    for source in validation.script_rows:
        clip_id = source["id"]
        review = review_by_id[clip_id]
        filename = nfc(source["filename"])
        source_path = validation.audio_by_name[filename]
        samples, native_rate = _read_clip(source_path)
        if native_rate != 8000:
            divisor = gcd(native_rate, 8000)
            samples = signal.resample_poly(
                samples, 8000 // divisor, native_rate // divisor
            ).astype(np.float32)
        target = audio_output / f"{clip_id}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        sf.write(target, samples, 8000, subtype="PCM_16")
        requested_group = (review.get("group") or source.get("group") or "").strip()
        group = "staged_call" if requested_group == "staged_call" else "clean_control"
        source_type = review["source_type"].strip()
        quiet_room = review.get("quiet_room", "").strip().lower()
        clean_reference = source_type == "synthetic_tts" or (
            source_type == "human_recording" and quiet_room == "yes"
        )
        if group == "staged_call":
            clean_reference = False
        rows.append(
            {
                "id": f"team-{clip_id}",
                "source_clip_id": clip_id,
                "audio_path": os.path.relpath(target, manifest_path.parent).replace(os.sep, "/"),
                "language": source["language"],
                "reference": nfc(source["text"]),
                "speaker_id": review["speaker_id_confirmed"].strip(),
                "group": group,
                "source": "team_recordings",
                "source_type": source_type,
                "license": "Team-provided; permission and consent status not independently verified",
                "reference_provenance": "team script",
                "native_sample_rate": native_rate,
                "split": validation.split_by_id[clip_id],
                "split_basis": validation.split_basis,
                "clean_reference_available": clean_reference,
                "noise_type": "",
                "snr_db_nominal": "",
                "snr_db_measured": "",
                "snr_basis": "",
            }
        )
    write_manifest(manifest_path, rows)
    manifests = source_manifest_paths(manifest_path.parent)
    write_provenance(manifest_path.parent.parent / "PROVENANCE.md", manifests)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-dir", type=Path, default=ROOT / "data" / "team")
    parser.add_argument("--review", type=Path)
    parser.add_argument("--audio-output", type=Path, default=ROOT / "data" / "audio" / "team")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data" / "manifests" / "team.csv")
    parser.add_argument("--allow-sentence-split", action="store_true")
    args = parser.parse_args()
    review = review_path(args.team_dir, args.review)
    if review.name == "voice_group_review_with_audit.csv" and args.review is None:
        print(f"Using available review sheet: {review}")
        print("Expected voice_group_review.csv is absent; the review sheet was not modified.")
    try:
        rows = import_team(
            args.team_dir, args.audio_output.resolve(), args.manifest.resolve(), review,
            allow_sentence_split=args.allow_sentence_split,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"TEAM IMPORT ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Imported team clips: {len(rows)}")
    print(f"split_basis={rows[0]['split_basis'] if rows else 'unresolved'}")
    print(
        "source_type mix: "
        + str({key: sum(row["source_type"] == key for row in rows) for key in ("human_recording", "synthetic_tts")})
    )
    print(f"Manifest: {args.manifest.resolve()}")
    print("Audio is local-only; source permission/consent has not been independently verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
