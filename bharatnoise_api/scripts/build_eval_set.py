"""Build licensed, balanced telephone-channel mixtures from reviewed manifests."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

try:
    from .data_common import MANIFEST_FIELDS, truthy, write_manifest
    from .plan_budget import VALID_CANDIDATES, VALID_MODELS
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.data_common import MANIFEST_FIELDS, truthy, write_manifest
    from scripts.plan_budget import VALID_CANDIDATES, VALID_MODELS

_BANDPASS = signal.butter(4, [300, 3400], btype="bandpass", fs=8000, output="sos")
MIXTURE_SNRS = (-5.0, 0.0, 5.0)


def _load_8k(path: Path) -> np.ndarray:
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    if samples.size == 0:
        raise ValueError(f"{path.name} decoded to an empty signal.")
    mono = samples.mean(axis=1).astype(np.float32)
    if int(rate) != 8000:
        from math import gcd

        divisor = gcd(int(rate), 8000)
        mono = signal.resample_poly(mono, 8000 // divisor, int(rate) // divisor).astype(np.float32)
    return mono


def _noise_length(noise: np.ndarray, length: int, rng: np.random.Generator) -> np.ndarray:
    if not len(noise):
        raise ValueError("Noise input is empty.")
    if len(noise) < length:
        noise = np.tile(noise, int(np.ceil(length / len(noise))))
    start = int(rng.integers(0, len(noise) - length + 1))
    return noise[start:start + length]


def _mulaw(samples: np.ndarray) -> np.ndarray:
    """Encode and decode G.711 mu-law using deterministic 8-bit companded values."""
    clipped = np.clip(samples, -1.0, 1.0)
    mu = 255.0
    compressed = np.sign(clipped) * np.log1p(mu * np.abs(clipped)) / np.log1p(mu)
    quantized = np.round((compressed + 1.0) * 127.5) / 127.5 - 1.0
    magnitude = np.expm1(np.abs(quantized) * np.log1p(mu)) / mu
    return (np.sign(quantized) * magnitude).astype(np.float32)


def _stable_seed(seed: int, *parts: str) -> int:
    digest = hashlib.sha256("\0".join((str(seed), *parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _legacy_split_by_speaker(
    rows: list[dict[str, str]], fraction: float, seed: int
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    speakers = sorted({row.get("speaker_id", "") for row in rows if row.get("speaker_id", "")})
    random.Random(seed).shuffle(speakers)
    if len(speakers) < 2:
        raise ValueError("Legacy plumbing fixture needs at least two labeled speakers to split.")
    test_speakers = set(speakers[:min(len(speakers) - 1, max(1, round(len(speakers) * fraction)))])
    return (
        [row for row in rows if row.get("speaker_id", "") not in test_speakers],
        [row for row in rows if row.get("speaker_id", "") in test_speakers],
    )


def _licensed_noise(noise_dir: Path) -> dict[tuple[str, str], list[Path]]:
    license_path = noise_dir / "LICENSES.csv"
    if not license_path.is_file():
        raise ValueError(f"Noise license manifest is required: {license_path}")
    with license_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"file", "type", "source", "license", "note"}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise ValueError("LICENSES.csv must contain file,type,source,license,note.")
        rows = list(reader)
    indexed: dict[tuple[str, str], list[Path]] = {}
    listed: set[Path] = set()
    root = noise_dir.resolve()
    for row in rows:
        relative = Path(row["file"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe noise path in LICENSES.csv: {row['file']!r}")
        audio_path = (noise_dir / relative).resolve()
        if root not in audio_path.parents:
            raise ValueError(f"Noise path escapes the license directory: {row['file']!r}")
        if not audio_path.is_file():
            raise ValueError(f"Licensed noise file is missing: {row['file']!r}")
        if not row["type"].strip() or not row["source"].strip() or not row["license"].strip():
            raise ValueError(f"Noise license row has missing provenance for {row['file']!r}")
        if audio_path in listed:
            raise ValueError(f"Noise file is listed more than once: {row['file']!r}")
        listed.add(audio_path)
        parts = relative.parts
        if len(parts) < 3 or parts[1] not in {"dev", "test"}:
            continue
        indexed.setdefault((row["type"].strip(), parts[1]), []).append(audio_path)
    actual = {path.resolve() for path in noise_dir.rglob("*.wav")}
    unlicensed = sorted(path.relative_to(root).as_posix() for path in actual - listed)
    if unlicensed:
        raise ValueError(f"Refusing unlicensed noise files: {', '.join(unlicensed[:8])}")
    return indexed


def _read_manifest(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"Manifest does not exist: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Manifest has no header: {path}")
        required = {"id", "source_clip_id", "audio_path", "language", "reference",
                    "speaker_id", "source_type", "split", "clean_reference_available"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"Manifest is missing required provenance fields: {', '.join(sorted(missing))}"
            )
        return list(reader)


def _phone_channel(samples: np.ndarray) -> np.ndarray:
    if len(samples) < 32:
        raise ValueError("Base audio is too short for telephone-channel filtering.")
    return signal.sosfiltfilt(_BANDPASS, samples).astype(np.float32)


def _relative_sisdr(reference: np.ndarray, estimate: np.ndarray) -> float | None:
    if reference.shape != estimate.shape or reference.size == 0:
        return None
    ref = reference.astype(np.float64) - np.mean(reference)
    est = estimate.astype(np.float64) - np.mean(estimate)
    denominator = float(np.dot(ref, ref))
    if denominator <= 1e-20:
        return None
    target = float(np.dot(est, ref) / denominator) * ref
    residual = est - target
    residual_power = float(np.dot(residual, residual))
    target_power = float(np.dot(target, target))
    return 10.0 * math.log10(max(target_power, 1e-20) / max(residual_power, 1e-20))


def build_balanced(
    manifest_path: Path,
    noise_dir: Path,
    output_dir: Path,
    *,
    mixtures_per_clip: int = 2,
    seed: int = 0,
    splits: tuple[str, ...] = ("dev",),
) -> dict[str, object]:
    """Build deterministic balanced noise/SNR cells, preserving source clusters."""
    if mixtures_per_clip < 1:
        raise ValueError("mixtures_per_clip must be at least one.")
    noise_dir = noise_dir.resolve()
    base_rows = _read_manifest(manifest_path)
    noise_index = _licensed_noise(noise_dir)
    selected_splits = set(splits)
    if selected_splits - {"dev", "test"} or not selected_splits:
        raise ValueError("splits must contain dev and/or test.")
    eligible: list[dict[str, str]] = []
    for row in base_rows:
        split = row["split"].strip()
        if split not in {"dev", "test"}:
            raise ValueError(f"Manifest row {row.get('id', '')!r} has no explicit dev/test split.")
        if split in selected_splits and truthy(row["clean_reference_available"]):
            if not row["source_clip_id"].strip():
                raise ValueError(f"Manifest row {row['id']!r} has no source_clip_id.")
            if not row["reference"].strip():
                raise ValueError(f"Manifest row {row['id']!r} has a clean flag but no reference.")
            if not row["source_type"].strip():
                raise ValueError(f"Manifest row {row['id']!r} has no source_type.")
            eligible.append(row)
    if not eligible:
        raise ValueError("No explicitly clean-reference-eligible base clips are available for the selected split.")
    for split in selected_splits:
        if not any(row["split"].strip() == split for row in eligible):
            raise ValueError(f"No clean-reference-eligible base clips are available for {split}.")

    noise_types_by_split = {
        split: sorted(noise_type for noise_type, noise_split in noise_index if noise_split == split)
        for split in selected_splits
    }
    for split in selected_splits:
        if not noise_types_by_split[split]:
            raise ValueError(f"No licensed {split} noise segments are available.")

    output_dir.mkdir(parents=True, exist_ok=True)
    generated: dict[str, list[dict[str, object]]] = {"dev": [], "test": []}
    build_entries: list[dict[str, object]] = []
    snr_counts: dict[str, int] = {}
    independent_counts: dict[str, dict[str, int]] = {}
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in eligible:
        grouped.setdefault((row["split"].strip(), row["language"].strip()), []).append(row)

    for (split, language), source_rows in sorted(grouped.items()):
        noise_types = noise_types_by_split[split]
        cells = [(noise_type, snr) for noise_type in noise_types for snr in MIXTURE_SNRS]
        total_mixtures = len(source_rows) * mixtures_per_clip
        rng = np.random.default_rng(_stable_seed(seed, split, language, "cells"))
        cell_order = rng.permutation(len(cells)).tolist()
        assignments = [cells[index] for index in (cell_order * math.ceil(total_mixtures / len(cells)))[:total_mixtures]]
        source_rng = np.random.default_rng(_stable_seed(seed, split, language, "sources"))
        source_order = source_rng.permutation(len(source_rows)).tolist()
        source_rows = [source_rows[index] for index in source_order]
        cell_cursor = 0
        for source in source_rows:
            source_audio = Path(source["audio_path"])
            if not source_audio.is_absolute():
                source_audio = manifest_path.parent / source_audio
            clean_original = _load_8k(source_audio)
            speech = _phone_channel(clean_original)
            speech_power = float(np.mean(speech.astype(np.float64) ** 2))
            if speech_power <= 0:
                raise ValueError(f"Base clip {source['id']!r} has zero power after telephone filtering.")
            for repeat in range(mixtures_per_clip):
                noise_type, target_snr = assignments[cell_cursor]
                cell_cursor += 1
                available_noise = noise_index[(noise_type, split)]
                rng = np.random.default_rng(
                    _stable_seed(seed, split, language, source["source_clip_id"], str(repeat), noise_type, str(target_snr))
                )
                noise_file = available_noise[int(rng.integers(0, len(available_noise)))]
                noise = _phone_channel(_noise_length(_load_8k(noise_file), len(speech), rng))
                noise_power = float(np.mean(noise.astype(np.float64) ** 2))
                if noise_power <= 0:
                    raise ValueError(f"Licensed noise file {noise_file.name!r} has zero power.")
                scale = float(np.sqrt(speech_power / (noise_power * 10 ** (target_snr / 10))))
                mixture = speech + scale * noise
                measured_snr = 10.0 * math.log10(
                    speech_power / float(np.mean((scale * noise).astype(np.float64) ** 2))
                )
                si_sdr = _relative_sisdr(speech, mixture)
                peak = float(np.max(np.abs(mixture)))
                if peak > 1.0:
                    mixture = mixture / peak
                channel_audio = _mulaw(mixture)
                base_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", source["source_clip_id"]).strip("._")
                mixture_id = f"{base_id}_{noise_type}_snr{target_snr:g}_r{repeat + 1}"
                relative_audio = Path("audio") / split / f"{mixture_id}.wav"
                target_audio_path = output_dir / relative_audio
                target_audio_path.parent.mkdir(parents=True, exist_ok=True)
                sf.write(target_audio_path, np.clip(channel_audio, -1.0, 1.0), 8000, subtype="PCM_16")
                row: dict[str, object] = {
                    **source,
                    "id": mixture_id,
                    "source_clip_id": source["source_clip_id"],
                    "audio_path": relative_audio.as_posix(),
                    "source": "synthetic_mixture",
                    "base_source_type": source["source_type"],
                    "source_type": "synthetic_mixture",
                    "group": "synthetic_mixture",
                    "noise_type": noise_type,
                    "snr_db_nominal": target_snr,
                    "snr_db_measured": measured_snr,
                    "snr_basis": "measured_vs_clean",
                    "clean_reference_available": True,
                }
                generated[split].append(row)
                snr_counts[f"{split}/{language}/{noise_type}/{target_snr:g}"] = (
                    snr_counts.get(f"{split}/{language}/{noise_type}/{target_snr:g}", 0) + 1
                )
                build_entries.append(
                    {
                        "id": mixture_id,
                        "source_clip_id": source["source_clip_id"],
                        "split": split,
                        "language": language,
                        "noise_type": noise_type,
                        "noise_license_file": noise_file.relative_to(noise_dir).as_posix(),
                        "snr_db_nominal": target_snr,
                        "snr_db_measured": measured_snr,
                        "snr_basis": "measured_vs_clean",
                        "si_sdr_db": si_sdr,
                        "seed": seed,
                    }
                )

    for split in selected_splits:
        split_rows = generated[split]
        write_manifest(output_dir / f"{split}.csv", split_rows)
        independent_counts[split] = {
            language: len({row["source_clip_id"] for row in eligible
                           if row["split"].strip() == split and row["language"].strip() == language})
            for language in sorted({row["language"].strip() for row in eligible if row["split"].strip() == split})
        }
    (output_dir / "build_log.json").write_text(
        json.dumps(
            {
                "seed": seed,
                "design": "balanced noise_type x nominal_SNR within split and language",
                "mixtures_per_clip": mixtures_per_clip,
                "noise_types": {split: noise_types_by_split[split] for split in sorted(selected_splits)},
                "snr_db_nominal": list(MIXTURE_SNRS),
                "cell_counts": snr_counts,
                "independent_source_clips": independent_counts,
                "sources": build_entries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "rows": generated,
        "independent_source_clips": independent_counts,
        "cell_counts": snr_counts,
        "log_path": output_dir / "build_log.json",
    }


def _legacy_plumbing(args: argparse.Namespace) -> int:
    """Keep the original synthetic-only mock integration fixture out of benchmarks."""
    if not args.manifest or not args.noise_dir:
        raise ValueError("Legacy plumbing mode requires --manifest and --noise-dir.")
    if args.include_test:
        raise ValueError("Legacy plumbing mode cannot build held-out/test mixtures.")
    _licensed_noise(args.noise_dir)
    with args.manifest.open(newline="", encoding="utf-8-sig") as handle:
        clean_rows = list(csv.DictReader(handle))
    rng = np.random.default_rng(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = args.output_dir / "audio"
    audio_dir.mkdir(exist_ok=True)
    noise_types = sorted(path for path in args.noise_dir.iterdir() if path.is_dir())
    generated: list[dict[str, str]] = []
    log_entries: list[dict[str, object]] = []
    snr_estimates: list[dict[str, float | str]] = []
    for source in clean_rows:
        clean_audio = _load_8k((args.manifest.parent / source["audio_path"]).resolve())
        speech = _phone_channel(clean_audio)
        speech_power = float(np.mean(speech.astype(np.float64) ** 2))
        if speech_power <= 0:
            continue
        for noise_folder in noise_types:
            noise_files = sorted(p for p in noise_folder.rglob("*.wav") if p.is_file())
            if not noise_files:
                continue
            noise_path = noise_files[int(rng.integers(0, len(noise_files)))]
            noise = _phone_channel(_noise_length(_load_8k(noise_path), len(speech), rng))
            noise_power = float(np.mean(noise.astype(np.float64) ** 2))
            if noise_power <= 0:
                continue
            for snr in args.snrs:
                scale = float(np.sqrt(speech_power / (noise_power * 10 ** (snr / 10))))
                mixture = speech + scale * noise
                peak = float(np.max(np.abs(mixture)))
                if peak > 1.0:
                    mixture = mixture / peak
                channel_audio = _mulaw(mixture)
                mixture_id = f"{source['id']}_{noise_folder.name}_snr{snr:g}"
                filename = f"{len(generated):06d}.wav"
                sf.write(audio_dir / filename, np.clip(channel_audio, -1, 1), 8000, subtype="PCM_16")
                group = f"{noise_folder.name}_snr{snr:g}"
                generated.append(
                    {
                        "id": mixture_id, "audio_path": f"audio/{filename}",
                        "language": source["language"], "reference": source["reference"],
                        "speaker_id": source["speaker_id"], "group": group,
                        "noise_type": noise_folder.name, "target_snr_db": str(snr),
                    }
                )
                measured_snr = 10 * np.log10(speech_power / (scale**2 * noise_power))
                snr_estimates.append({"id": mixture_id, "snr_db_measured": float(measured_snr)})
                log_entries.append(
                    {
                        "id": mixture_id, "seed": args.seed, "target_snr_db": snr,
                        "measured_linear_mix_snr_db": float(measured_snr),
                        "telephone_channel": "300-3400 Hz band-limit followed by 8-bit G.711 mu-law",
                        "benchmark_eligible": False,
                    }
                )
    if not generated:
        raise ValueError("Legacy fixture manifest generated no mixtures.")
    if not 0 <= args.test_fraction < 1:
        raise ValueError("test-fraction must be in [0, 1).")
    dev, test = _legacy_split_by_speaker(generated, args.test_fraction, args.seed)
    for name, rows in (("dev", dev), ("test", test)):
        with (args.output_dir / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(generated[0]))
            writer.writeheader()
            writer.writerows(rows)
        ids = {row["id"] for row in rows}
        with (args.output_dir / f"snr_{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["id", "snr_db_measured"])
            writer.writeheader()
            writer.writerows(row for row in snr_estimates if row["id"] in ids)
    (args.output_dir / "build_log.json").write_text(
        json.dumps({"seed": args.seed, "sources": log_entries, "benchmark_eligible": False}, indent=2),
        encoding="utf-8",
    )
    print("Built explicitly non-benchmark synthetic plumbing fixtures.")
    print(f"Generated mixtures: {len(generated)}; independent source clips: {len(clean_rows)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Reviewed clean-base manifest")
    parser.add_argument("--noise-dir", type=Path, required=True, help="Contains LICENSES.csv and type/split segments")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mixtures-per-clip", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--include-test", action="store_true", help="Build held-out mixtures only after freeze.")
    parser.add_argument("--candidates", nargs="+", choices=VALID_CANDIDATES, default=["C0", "C1", "C2"])
    parser.add_argument("--models", nargs="+", choices=VALID_MODELS, default=["v2.5"])
    parser.add_argument("--legacy-plumbing-only", action="store_true")
    parser.add_argument("--snrs", type=float, nargs="+", default=[0.0, 5.0, 10.0])
    parser.add_argument("--test-fraction", type=float, default=0.2)
    args = parser.parse_args()
    try:
        if args.legacy_plumbing_only:
            return _legacy_plumbing(args)
        if args.include_test:
            try:
                from .freeze_config import verify_frozen_config
            except ImportError:
                from scripts.freeze_config import verify_frozen_config

            verify_frozen_config()
        selected_splits = ("dev", "test") if args.include_test else ("dev",)
        result = build_balanced(
            args.manifest, args.noise_dir, args.output_dir,
            mixtures_per_clip=args.mixtures_per_clip, seed=args.seed, splits=selected_splits,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"EVAL SET BUILD ERROR: {exc}", file=sys.stderr)
        return 2
    row_count = sum(len(result["rows"][split]) for split in selected_splits)
    audio_seconds = sum(
        sf.info(args.output_dir / row["audio_path"]).duration
        for split in selected_splits for row in result["rows"][split]
    )
    calls = row_count * len(set(args.candidates)) * len(set(args.models))
    print(f"Built mixtures: {row_count}; split(s): {', '.join(selected_splits)}")
    print(f"Independent source clips by split/language: {result['independent_source_clips']}")
    print(f"Planned API calls for {args.candidates} x {args.models}: {calls}")
    print(f"Planned audio: {audio_seconds * len(set(args.candidates)) * len(set(args.models)) / 60:.6f} minutes")
    print(f"Build log: {result['log_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
