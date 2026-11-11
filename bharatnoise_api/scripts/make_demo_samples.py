"""Generate labeled synthetic tones and noise for pipeline plumbing, never benchmark results."""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 8000
ENGLISH_SCRIPT = "This synthetic utterance is for audio pipeline testing."
HINDI_SCRIPT = "यह ऑडियो पाइपलाइन परीक्षण के लिए कृत्रिम वाक्य है।"


def _tone_clip(index: int, rng: np.random.Generator) -> np.ndarray:
    duration = 2.0 + 0.1 * (index % 3)
    time_axis = np.arange(round(duration * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    modulation = 0.25 + 0.75 * np.maximum(0, np.sin(2 * np.pi * (2.4 + index * 0.11) * time_axis))
    carrier = (
        np.sin(2 * np.pi * (150 + index * 17) * time_axis)
        + 0.45 * np.sin(2 * np.pi * (310 + index * 9) * time_axis)
        + 0.2 * np.sin(2 * np.pi * (620 + index * 13) * time_axis)
    )
    signal_audio = 0.15 * modulation * carrier
    return (signal_audio + 0.002 * rng.standard_normal(len(time_axis))).astype(np.float32)


def _noise_samples(name: str, seed: int, seconds: float = 10.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    length = round(seconds * SAMPLE_RATE)
    time_axis = np.arange(length, dtype=np.float64) / SAMPLE_RATE
    if name == "white":
        result = rng.standard_normal(length)
    elif name == "babble_like":
        white = rng.standard_normal(length)
        result = signal.sosfilt(signal.butter(4, [250, 3200], btype="bandpass", fs=SAMPLE_RATE, output="sos"), white)
        for _ in range(7):
            frequency = float(rng.uniform(120, 1100))
            phase = float(rng.uniform(0, 2 * math.pi))
            envelope = 0.4 + 0.6 * np.maximum(0, np.sin(2 * np.pi * rng.uniform(1, 4) * time_axis + phase))
            result += 0.2 * envelope * np.sin(2 * np.pi * frequency * time_axis + phase)
    elif name == "mains_hum":
        result = sum((1 / harmonic) * np.sin(2 * np.pi * 50 * harmonic * time_axis) for harmonic in range(1, 6))
        result += 0.015 * rng.standard_normal(length)
    elif name == "horn_like":
        result = np.zeros(length, dtype=np.float64)
        for start in range(0, length, SAMPLE_RATE):
            offset = start + int(rng.integers(0, SAMPLE_RATE // 2))
            duration_samples = min(int(0.18 * SAMPLE_RATE), length - offset)
            if duration_samples <= 0:
                continue
            pulse_time = np.arange(duration_samples) / SAMPLE_RATE
            envelope = np.sin(np.linspace(0, np.pi, duration_samples)) ** 2
            pulse = np.sin(2 * np.pi * 370 * pulse_time) + 0.55 * np.sin(2 * np.pi * 490 * pulse_time)
            result[offset:offset + duration_samples] += envelope * pulse
        result += 0.01 * rng.standard_normal(length)
    else:
        raise ValueError(f"Unknown synthetic noise type: {name}")
    peak = float(np.max(np.abs(result))) if len(result) else 0.0
    return (0.25 * result / peak if peak else result).astype(np.float32)


def _say_voices() -> tuple[list[tuple[str, str]], str | None]:
    executable = shutil.which("say")
    if sys.platform != "darwin" or executable is None:
        return [], "macOS `say` is unavailable; generated tone clips only."
    try:
        result = subprocess.run(
            [executable, "-v", "?"], capture_output=True, text=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"Could not list `say` voices ({type(exc).__name__}); generated tone clips only."
    voices: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, locale = parts[0], parts[1]
        if locale.startswith("en_") and name not in {voice for voice, _ in voices}:
            voices.append((name, "en-IN"))
        elif locale.startswith("hi_") and name not in {voice for voice, _ in voices}:
            voices.append((name, "hi-IN"))
    return voices, None if voices else "No Hindi or English `say` voices are installed."


def _write_tts(
    samples_dir: Path, voices: list[tuple[str, str]], output_rows: list[dict[str, str]],
    index_rows: list[dict[str, str]],
) -> list[str]:
    executable = shutil.which("say")
    if executable is None:
        return []
    written: list[str] = []
    used_languages: set[str] = set()
    for voice, language in voices:
        if language in used_languages:
            continue
        script = HINDI_SCRIPT if language == "hi-IN" else ENGLISH_SCRIPT
        clip_id = f"synthetic-tts-{language.lower()}-{voice.lower()}"
        destination = samples_dir / "audio" / f"{clip_id}.aiff"
        try:
            subprocess.run(
                [executable, "-v", voice, "-o", str(destination), script],
                capture_output=True, check=True, timeout=30,
            )
            samples, sample_rate = sf.read(destination, dtype="float32", always_2d=True)
        except (OSError, RuntimeError, sf.LibsndfileError, subprocess.SubprocessError):
            destination.unlink(missing_ok=True)
            continue
        if not len(samples) or sample_rate <= 0:
            destination.unlink(missing_ok=True)
            continue
        speaker_id = f"tts-{voice}"
        reference = script
        entry = {
            "id": clip_id, "audio_path": f"audio/{destination.name}", "language": language,
            "reference": reference, "speaker_id": speaker_id, "group": "synthetic_tts",
        }
        output_rows.append(entry)
        index_rows.append({
            "id": clip_id, "language": language, "group": "synthetic_tts",
            "reference": reference, "description": "synthetic TTS, not real speech",
            "file": f"audio/{destination.name}",
        })
        used_languages.add(language)
        written.append(clip_id)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-dir", type=Path, default=ROOT / "samples")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "demo_samples")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--tone-clips", type=int, default=6)
    args = parser.parse_args()
    if args.tone_clips < 2:
        parser.error("--tone-clips must be at least two.")
    samples_dir = args.samples_dir.resolve()
    data_dir = args.data_dir.resolve()
    audio_dir = samples_dir / "audio"
    noise_dir = data_dir / "noise"
    audio_dir.mkdir(parents=True, exist_ok=True)
    noise_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    manifest_rows: list[dict[str, str]] = []
    index_rows: list[dict[str, str]] = []
    for index in range(args.tone_clips):
        clip_id = f"synthetic-tone-{index:02d}"
        filename = f"{clip_id}.wav"
        sf.write(audio_dir / filename, _tone_clip(index, rng), SAMPLE_RATE, subtype="PCM_16")
        manifest_rows.append({
            "id": clip_id, "audio_path": f"audio/{filename}", "language": "en-IN",
            "reference": "", "speaker_id": f"tone-speaker-{index:02d}", "group": "synthetic_tone",
        })
        index_rows.append({
            "id": clip_id, "language": "en-IN", "group": "synthetic_tone",
            "description": "synthetic, not real speech", "file": f"audio/{filename}",
        })

    voices, reason = _say_voices()
    tts_ids = _write_tts(samples_dir, voices, manifest_rows, index_rows)
    for name in ("white", "babble_like", "mains_hum", "horn_like"):
        folder = noise_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        sf.write(folder / f"{name}.wav", _noise_samples(name, args.seed + len(name)), SAMPLE_RATE, subtype="PCM_16")

    fields = ["id", "audio_path", "language", "reference", "speaker_id", "group"]
    with (samples_dir / "demo_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(manifest_rows)
    (samples_dir / "index.json").write_text(json.dumps(index_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (data_dir / "build_log.json").write_text(json.dumps({
        "seed": args.seed, "description": "synthetic, not real speech",
        "tone_clip_ids": [row["id"] for row in manifest_rows if row["group"] == "synthetic_tone"],
        "tts_clip_ids": tts_ids,
        "noise_types": ["white", "babble_like", "mains_hum", "horn_like"],
        "tts_note": reason,
        "benchmark_eligible": False,
    }, indent=2), encoding="utf-8")
    print(f"Generated {len(index_rows)} synthetic sample entries; they are not benchmark results.")
    if reason:
        print(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
