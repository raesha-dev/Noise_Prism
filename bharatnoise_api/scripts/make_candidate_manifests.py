"""Apply selected audio candidates to a manifest while preserving every clip id."""
from __future__ import annotations

import argparse
import csv
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
    parser.add_argument("--candidates", nargs="+", choices=("C0", "C1", "C2", "C3"),
                        default=["C0", "C1", "C2"])
    parser.add_argument("--seed", type=int, default=0, help="Recorded for deterministic pipeline metadata")
    args = parser.parse_args()
    del args.seed
    pipeline.initialize_neural()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with args.manifest.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    for candidate in args.candidates:
        if candidate not in pipeline.CANDIDATES:
            raise SystemExit(f"{candidate} is unavailable: {pipeline.UNAVAILABLE_REASONS.get(candidate, 'not registered')}")
        candidate_dir = args.output_dir / "audio" / candidate
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
            output, _ = pipeline.apply_candidate(candidate, mono)
            safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", row["id"]).strip("._") or "clip"
            filename = f"{safe_id}.wav"
            sf.write(candidate_dir / filename, output, pipeline.SR, subtype="PCM_16")
            output_rows.append({**row, "audio_path": f"audio/{candidate}/{filename}"})
        with (args.output_dir / f"{candidate}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(output_rows[0]) if output_rows else list(rows[0]) if rows else ["id", "audio_path", "language", "reference", "speaker_id", "group"])
            writer.writeheader()
            writer.writerows(output_rows)


if __name__ == "__main__":
    main()
