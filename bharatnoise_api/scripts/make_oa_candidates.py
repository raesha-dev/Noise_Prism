"""Generate C2 plus additive observation-addition variants for development only."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

try:
    from .. import pipeline
    from ..audio.suppress import load_config
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import pipeline
    from audio.suppress import load_config

LAMBDA_SWEEP = (0.0, 0.05, 0.1, 0.2, 0.3, 0.4)


def _read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"id", "audio_path", "language", "reference", "split"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Manifest is missing columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    if not rows:
        raise ValueError("Development manifest contains no clips.")
    if any(row.get("split", "").strip().lower() not in {"dev", "development"} for row in rows):
        raise ValueError("Observation-addition candidates may only be generated from development rows.")
    return rows


def generate(manifest: Path, output_dir: Path, lambdas: list[float]) -> int:
    if not lambdas or len(set(lambdas)) != len(lambdas) or any(value not in LAMBDA_SWEEP for value in lambdas):
        raise ValueError(f"Each lambda must be a unique value from {LAMBDA_SWEEP}.")
    rows = _read_manifest(manifest)
    base_config = load_config()
    output_dir.mkdir(parents=True, exist_ok=True)
    for coefficient in lambdas:
        label = f"C2+OA({coefficient:g})"
        safe_label = f"C2_OA_lambda{coefficient:g}"
        audio_dir = output_dir / "audio" / safe_label
        audio_dir.mkdir(parents=True, exist_ok=True)
        suppress_config = {
            **base_config,
            "observation_blend": [{"min_snr_db": -100, "lambda": coefficient}],
            "fitted": False,
        }
        output_rows = []
        for row in rows:
            source = Path(row["audio_path"])
            if not source.is_absolute():
                source = manifest.parent / source
            samples, rate = sf.read(source, dtype="float32", always_2d=True)
            mono = samples.mean(axis=1).astype(np.float32)
            if rate != pipeline.SR:
                divisor = gcd(int(rate), pipeline.SR)
                mono = signal.resample_poly(mono, pipeline.SR // divisor, int(rate) // divisor).astype(np.float32)
            enhanced, metadata = pipeline.apply_candidate("C2", mono, suppress_config=suppress_config)
            safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", row["id"]).strip("._") or "clip"
            filename = f"{safe_id}.wav"
            sf.write(audio_dir / filename, enhanced, pipeline.SR, subtype="PCM_16")
            output_rows.append({
                **row,
                "audio_path": (Path("audio") / safe_label / filename).as_posix(),
                "candidate": label,
                "enhancement_candidate": "C2+OA",
                "oa_lambda": coefficient,
                "processing_parameters": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            })
        fields = list(output_rows[0])
        with (output_dir / f"{safe_label}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(output_rows)
        print(f"{label}: {len(output_rows)} development clips; unfitted parameters recorded.")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="A dev-only manifest; for the sweep, preselect the 20 hardest dev mixtures.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--lambdas", type=float, nargs="+", default=list(LAMBDA_SWEEP))
    args = parser.parse_args()
    try:
        generate(args.manifest.resolve(), args.output_dir.resolve(), args.lambdas)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"OA CANDIDATE ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
