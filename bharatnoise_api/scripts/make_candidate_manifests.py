"""Apply selected audio candidates to a manifest while preserving every clip id."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import soundfile as sf

try:
    from .. import pipeline
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidates", nargs="+", choices=("C0", "C1", "C2", "C2B", "C2C", "C3"),
                        default=["C0", "C1", "C2"])
    parser.add_argument("--oa-lambdas", nargs="+", type=float, default=None,
                        help="C2C coefficients; defaults to the full configured experimental sweep.")
    parser.add_argument("--seed", type=int, default=0, help="Recorded with candidate generation metadata")
    args = parser.parse_args()
    oa_lambdas = args.oa_lambdas or [float(value) for value in pipeline.IMCRA_CONFIG["oa_sweep"]]
    if not oa_lambdas or any(not 0 <= value <= 0.4 for value in oa_lambdas):
        parser.error("--oa-lambdas values must be in [0, 0.4].")
    if len(set(oa_lambdas)) != len(oa_lambdas):
        parser.error("--oa-lambdas values must be unique.")
    pipeline.initialize_neural()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with args.manifest.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if any(row.get("split", "").strip().lower() in {"test", "held-out", "heldout"} for row in rows):
        try:
            from .freeze_config import verify_frozen_config
        except ImportError:
            from scripts.freeze_config import verify_frozen_config
        try:
            verify_frozen_config()
        except ValueError as exc:
            parser.error(f"Held-out candidate generation requires a matching frozen configuration: {exc}")
    combinations = [
        (candidate, coefficient)
        for candidate in dict.fromkeys(args.candidates)
        for coefficient in (oa_lambdas if candidate == "C2C" else [None])
    ]
    for candidate, coefficient in combinations:
        if candidate not in pipeline.CANDIDATES:
            raise SystemExit(f"{candidate} is unavailable: {pipeline.UNAVAILABLE_REASONS.get(candidate, 'not registered')}")
        candidate_name = f"C2C_lambda{coefficient:g}" if coefficient is not None else candidate
        candidate_dir = args.output_dir / "audio" / candidate_name
        candidate_dir.mkdir(parents=True, exist_ok=True)
        output_rows = []
        for row in rows:
            source = Path(row["audio_path"])
            if not source.is_absolute():
                source = args.manifest.parent / source
            audio, rate = sf.read(source, dtype="float32", always_2d=True)
            mono = audio.mean(axis=1)
            if rate != pipeline.SR:
                from scipy import signal
                from math import gcd

                divisor = gcd(int(rate), pipeline.SR)
                mono = signal.resample_poly(mono, pipeline.SR // divisor, int(rate) // divisor)
            output, metadata = pipeline.apply_candidate(candidate, mono, oa_lambda=coefficient)
            safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", row["id"]).strip("._") or "clip"
            filename = f"{safe_id}.wav"
            sf.write(candidate_dir / filename, output, pipeline.SR, subtype="PCM_16")
            output_rows.append({
                **row,
                "audio_path": f"audio/{candidate_name}/{filename}",
                "candidate": candidate_name,
                "enhancement_candidate": candidate,
                "pipeline_seed": args.seed,
                "oa_lambda": "" if coefficient is None else coefficient,
                "processing_parameters": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            })
        fields = list(output_rows[0]) if output_rows else (
            list(rows[0]) if rows else ["id", "audio_path", "language", "reference", "speaker_id", "group"]
        )
        for field in ("candidate", "enhancement_candidate", "pipeline_seed", "oa_lambda", "processing_parameters"):
            if field not in fields:
                fields.append(field)
        with (args.output_dir / f"{candidate_name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(output_rows)


if __name__ == "__main__":
    main()
