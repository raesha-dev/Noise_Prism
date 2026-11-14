"""Measure implemented SNR estimates on controlled, licensed development mixtures."""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

try:
    from .. import pipeline
    from .build_eval_set import (
        _licensed_noise,
        _load_8k,
        _noise_length,
        _phone_channel,
        _stable_seed,
    )
    from .data_common import truthy
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import pipeline
    from scripts.build_eval_set import (
        _licensed_noise,
        _load_8k,
        _noise_length,
        _phone_channel,
        _stable_seed,
    )
    from scripts.data_common import truthy

ROOT = Path(__file__).resolve().parents[1]
TARGET_SNRS_DB = (-5.0, 0.0, 5.0, 10.0, 15.0)
OUTPUT_FIELDS = [
    "estimator", "noise_type", "target_snr_db", "source_clips",
    "mean_absolute_error_db", "bias_db", "validated",
]


def _read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "id", "source_clip_id", "audio_path", "split", "clean_reference_available",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Clean manifest is missing: {', '.join(sorted(missing))}")
        rows = list(reader)
    eligible = [
        row for row in rows
        if row.get("split", "").strip().lower() in {"dev", "development"}
        and truthy(row.get("clean_reference_available", ""))
    ]
    if not eligible:
        raise ValueError("No explicitly clean-reference-eligible Dev rows were found.")
    if len({row.get("source_clip_id", "").strip() for row in eligible}) != len(eligible):
        raise ValueError("Dev manifest must contain one row per independent source clip.")
    return eligible


def validate(
    manifest_path: Path,
    noise_dir: Path,
    *,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Return descriptive errors; no estimator is marked validated automatically."""
    manifest_path = manifest_path.resolve()
    noise_dir = noise_dir.resolve()
    rows = _read_manifest(manifest_path)
    noise_index = _licensed_noise(noise_dir)
    noise_types = sorted(
        noise_type for noise_type, split in noise_index if split == "dev"
    )
    if not noise_types:
        raise ValueError("No licensed Dev noise segments are available.")
    errors: dict[tuple[str, float], list[float]] = defaultdict(list)
    source_ids: dict[tuple[str, float], set[str]] = defaultdict(set)

    for row in rows:
        audio_path = Path(row["audio_path"])
        if not audio_path.is_absolute():
            audio_path = manifest_path.parent / audio_path
        clean = _phone_channel(_load_8k(audio_path))
        clean_power = float(np.mean(clean.astype(np.float64) ** 2))
        if clean_power <= 0:
            continue
        source_id = row["source_clip_id"].strip()
        for noise_type in noise_types:
            segments = noise_index[(noise_type, "dev")]
            rng = np.random.default_rng(
                _stable_seed(seed, source_id, noise_type, "snr-validation")
            )
            noise_path = segments[int(rng.integers(0, len(segments)))]
            noise = _phone_channel(_noise_length(_load_8k(noise_path), len(clean), rng))
            noise_power = float(np.mean(noise.astype(np.float64) ** 2))
            if noise_power <= 0:
                continue
            for target_snr in TARGET_SNRS_DB:
                scale = math.sqrt(
                    clean_power / (noise_power * 10 ** (target_snr / 10))
                )
                mixture = clean + scale * noise
                peak = float(np.max(np.abs(mixture)))
                if peak > 1:
                    mixture = mixture / peak
                measured_snr = 10 * math.log10(
                    clean_power
                    / float(np.mean((scale * noise).astype(np.float64) ** 2))
                )
                estimated = pipeline.analyze(mixture).get("stationary_snr_db_est")
                key = (noise_type, target_snr)
                source_ids[key].add(source_id)
                if estimated is not None:
                    errors[key].append(float(estimated) - measured_snr)

    results: list[dict[str, Any]] = []
    for noise_type in noise_types:
        for target_snr in TARGET_SNRS_DB:
            key = (noise_type, target_snr)
            observed = errors[key]
            results.append({
                "estimator": "stationary_snr_db_est",
                "noise_type": noise_type,
                "target_snr_db": target_snr,
                "source_clips": len(source_ids[key]),
                "mean_absolute_error_db": (
                    float(np.mean(np.abs(observed))) if observed else None
                ),
                "bias_db": float(np.mean(observed)) if observed else None,
                "validated": False,
            })
    for noise_type in noise_types:
        results.append({
            "estimator": "wada_snr_db",
            "noise_type": noise_type,
            "target_snr_db": "",
            "source_clips": 0,
            "mean_absolute_error_db": None,
            "bias_db": None,
            "validated": False,
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--noise-dir", type=Path, default=ROOT / "data" / "noise")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "data" / "diagnostics" / "snr_estimator_validation.csv",
    )
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    try:
        rows = validate(args.manifest, args.noise_dir, seed=args.seed)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    except (OSError, RuntimeError, ValueError, csv.Error) as exc:
        print(f"SNR VALIDATION ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Descriptive estimator rows written: {len(rows)}")
    print("No estimator was marked validated; no acceptance threshold was configured.")
    print(f"Output: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
