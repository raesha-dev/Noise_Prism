"""Create deterministic telephone-channel mixtures at specified SNRs and speaker splits."""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

_BANDPASS = signal.butter(4, [300, 3400], btype="bandpass", fs=8000, output="sos")


def _load_8k(path: Path) -> np.ndarray:
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = samples.mean(axis=1).astype(np.float32)
    return signal.resample_poly(mono, 8000, int(rate)).astype(np.float32) if rate != 8000 else mono


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


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["id", "audio_path", "language", "reference", "speaker_id", "group"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _split_by_speaker(rows: list[dict[str, str]], fraction: float, seed: int) -> tuple[list, list]:
    speakers = sorted({row["speaker_id"] for row in rows})
    random.Random(seed).shuffle(speakers)
    n_test = min(max(1, round(len(speakers) * fraction)), len(speakers) - 1) if len(speakers) > 1 else 0
    test_speakers = set(speakers[:n_test])
    return [r for r in rows if r["speaker_id"] not in test_speakers], [r for r in rows if r["speaker_id"] in test_speakers]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Clean speech manifest")
    parser.add_argument("--noise-dir", type=Path, required=True, help="One noise-type subfolder per category")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--snrs", type=float, nargs="+", default=[0.0, 5.0, 10.0])
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.test_fraction < 1:
        parser.error("--test-fraction must be in [0, 1).")
    rng = np.random.default_rng(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = args.output_dir / "audio"
    audio_dir.mkdir(exist_ok=True)
    noise_types = sorted(path for path in args.noise_dir.iterdir() if path.is_dir())
    if not noise_types:
        parser.error("--noise-dir must contain at least one noise-type subfolder.")
    with args.manifest.open(newline="", encoding="utf-8-sig") as handle:
        clean_rows = list(csv.DictReader(handle))
    generated: list[dict[str, str]] = []
    log_entries: list[dict] = []
    snr_estimates: list[dict[str, float | str]] = []
    for source in clean_rows:
        clean_audio = _load_8k((args.manifest.parent / source["audio_path"]).resolve())
        speech = signal.sosfiltfilt(_BANDPASS, clean_audio).astype(np.float32)
        speech_power = float(np.mean(speech.astype(np.float64) ** 2))
        if speech_power <= 0:
            continue
        for noise_folder in noise_types:
            noise_files = sorted(p for p in noise_folder.rglob("*") if p.is_file())
            if not noise_files:
                continue
            noise_path = noise_files[int(rng.integers(0, len(noise_files)))]
            noise = _load_8k(noise_path)
            noise = _noise_length(noise, len(speech), rng)
            telephone_noise = signal.sosfiltfilt(_BANDPASS, noise).astype(np.float32)
            noise_power = float(np.mean(telephone_noise.astype(np.float64) ** 2))
            if noise_power <= 0:
                continue
            for snr in args.snrs:
                alpha = float(np.sqrt(speech_power / (noise_power * 10 ** (snr / 10))))
                mixture = speech + alpha * telephone_noise
                peak = float(np.max(np.abs(mixture))) if len(mixture) else 0.0
                if peak > 1.0:
                    mixture = mixture / peak
                channel_audio = _mulaw(mixture)
                base_id = f"{source['id']}_{noise_folder.name}_snr{snr:g}"
                filename = f"{len(generated):06d}.wav"
                sf.write(audio_dir / filename, np.clip(channel_audio, -1, 1), 8000, subtype="PCM_16")
                group = f"{noise_folder.name}_snr{snr:g}"
                generated.append({
                    "id": base_id, "audio_path": f"audio/{filename}", "language": source["language"],
                    "reference": source["reference"], "speaker_id": source["speaker_id"], "group": group,
                })
                measured_snr = 10 * np.log10(speech_power / (alpha**2 * noise_power))
                snr_estimates.append({"id": base_id, "snr_db_est": float(measured_snr)})
                log_entries.append({
                    "id": base_id, "seed": args.seed, "clean_source": source["audio_path"],
                    "noise_source": str(noise_path), "target_snr_db": snr,
                    "alpha": alpha, "measured_linear_mix_snr_db": measured_snr,
                    "telephone_channel": "300-3400 Hz band-limit followed by 8-bit G.711 mu-law",
                })
    dev, test = _split_by_speaker(generated, args.test_fraction, args.seed)
    _write(args.output_dir / "dev.csv", dev)
    _write(args.output_dir / "test.csv", test)
    split_ids = {
        "dev": {row["id"] for row in dev},
        "test": {row["id"] for row in test},
    }
    for split_name, clip_ids in split_ids.items():
        with (args.output_dir / f"snr_{split_name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["id", "snr_db_est"])
            writer.writeheader()
            writer.writerows(row for row in snr_estimates if row["id"] in clip_ids)
    (args.output_dir / "build_log.json").write_text(
        json.dumps({"seed": args.seed, "snrs_db": args.snrs, "sources": log_entries}, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
