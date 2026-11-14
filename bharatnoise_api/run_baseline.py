"""Run sequential Prisma baselines or plan calls from a labeled audio manifest."""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
from pathlib import Path

import soundfile as sf

try:
    from .env import load_environment
    from .gnani_client import FatalAPIError, GnaniClient, endpoint_mode
    from .metrics import score_utterance
    from .scripts.plan_budget import VALID_CANDIDATES, VALID_MODELS, manifest_durations, plan_manifest, project_path
except ImportError:
    from env import load_environment
    from gnani_client import FatalAPIError, GnaniClient, endpoint_mode
    from metrics import score_utterance
    from scripts.plan_budget import VALID_CANDIDATES, VALID_MODELS, manifest_durations, plan_manifest, project_path

ROOT = Path(__file__).resolve().parent


def _mock_output(path: Path) -> Path:
    data_root = Path(os.environ.get("BN_DATA_DIR", str(ROOT))).expanduser().resolve()
    name = path.name if path.name.startswith("MOCK_") else f"MOCK_{path.name}"
    return data_root / "results_mock" / name


def _output_for_run(base: Path, candidate: str, model: str, run_number: int, combinations: int) -> Path:
    if combinations == 1 and run_number == 1:
        return base
    suffix = f"_{candidate}_prisma-{model}_run{run_number}"
    return base.with_name(f"{base.stem}{suffix}{base.suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Manifest CSV")
    parser.add_argument("--output", type=Path, required=True, help="Results CSV output path")
    parser.add_argument("--model", choices=VALID_MODELS, default="v2.5")
    parser.add_argument("--format", choices=("verbatim", "transcribe", "codemix", "transliterate"),
                        default="verbatim", help="Gnani output format used for every row")
    parser.add_argument("--split", choices=("development", "held-out"),
                        help="Dataset split recorded in output; fit only from development results")
    parser.add_argument("--candidate", choices=VALID_CANDIDATES, default="C0", help="Candidate label stored with each row")
    parser.add_argument("--models", nargs="+", choices=VALID_MODELS, help="Models included in this serial run")
    parser.add_argument("--candidates", nargs="+", choices=VALID_CANDIDATES, help="Candidates included in this serial run")
    parser.add_argument("--runs", type=int, default=1, help="Sequential repeats per candidate/model")
    parser.add_argument("--run-id", default="run-1", help="Cache namespace; change for repeat runs")
    parser.add_argument("--seed", type=int, default=0, help="Deterministic seed metadata")
    parser.add_argument("--cache-dir", type=Path, help="Optional content cache directory")
    parser.add_argument("--dry-run", action="store_true", help="Validate the manifest and print planned calls without a key")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least one.")
    input_path = project_path(args.input).resolve()
    output_path = project_path(args.output).resolve()
    candidates = list(dict.fromkeys(args.candidates or [args.candidate]))
    models = list(dict.fromkeys(args.models or [args.model]))
    if args.format in {"codemix", "transliterate"} and any(model != "v3.0" for model in models):
        parser.error("--format codemix/transliterate requires Prisma v3.0 for every selected model.")

    if args.dry_run:
        try:
            calls, minutes = plan_manifest(input_path, candidates, models, args.runs)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Manifest: {input_path}")
        print(f"Candidates: {', '.join(candidates)}")
        print(f"Models: {', '.join(models)}")
        print(f"Runs per combination: {args.runs}")
        print(f"Planned API calls: {calls}")
        print(f"Planned audio: {minutes:.6f} minutes")
        print("No API requests were made.")
        return

    load_environment()
    try:
        manifest_durations(input_path)
    except ValueError as exc:
        parser.error(str(exc))
    mode = endpoint_mode()
    if mode == "unconfigured":
        parser.error("GNANI_API_KEY is not configured.")
    random.seed(args.seed)
    if mode == "mock":
        output_path = _mock_output(output_path)
        print(f"MOCK endpoint selected; writing benchmark rows only to {output_path}")
    try:
        if args.cache_dir is not None:
            cache_dir = project_path(args.cache_dir)
        elif mode == "mock":
            cache_dir = output_path.parent / ".cache"
        else:
            cache_dir = ROOT / "results" / ".cache"
        client = GnaniClient(cache_dir=cache_dir)
    except FatalAPIError as exc:
        parser.error(str(exc))

    try:
        with input_path.open(newline="", encoding="utf-8-sig") as source:
            entries = list(csv.DictReader(source))
    except OSError as exc:
        parser.error(f"Cannot read manifest {input_path}: {type(exc).__name__}.")
    if entries and not {"id", "audio_path", "language", "reference"} <= entries[0].keys():
        parser.error("Manifest must contain id, audio_path, language and reference columns.")
    test_run = args.split == "held-out" or any(
        row.get("split", "").strip().lower() in {"test", "held-out", "heldout"} for row in entries
    )
    if test_run:
        try:
            from .scripts.freeze_config import verify_frozen_config
        except ImportError:
            from scripts.freeze_config import verify_frozen_config
        try:
            frozen = verify_frozen_config()
        except ValueError as exc:
            parser.error(f"Held-out run requires a matching frozen configuration: {exc}")
        state_path = os.environ.get("BN_FROZEN_TEST_RUN_STATE", "")
        try:
            state = json.loads(Path(state_path).read_text(encoding="utf-8")) if state_path else {}
        except (OSError, json.JSONDecodeError):
            state = {}
        if (
            state.get("status") != "started"
            or state.get("frozen_sha256") != frozen.get("code_config_sha256")
        ):
            parser.error("Held-out requests must go through scripts/run_frozen_test.py exactly once.")
        if endpoint_mode() != "live":
            parser.error("Held-out requests require the official live Gnani endpoint.")

    combinations = len(candidates) * len(models)
    fields = [
        "id", "source_clip_id", "source_type", "split", "group", "language", "speaker_id",
        "noise_type", "target_snr_db",
        "model", "format", "sample_rate", "seed", "run", "candidate",
        "enhancement_candidate", "oa_lambda", "processing_parameters",
        "reference", "hypothesis", "error", "empty_transcript", "truncated",
        "detected_language_code", "wer", "cer", "substitutions", "deletions",
        "insertions", "word_count", "latency_ms", "duration_seconds",
        "api_call", "cache_hit",
    ]
    for candidate in candidates:
        for model in models:
            for run_number in range(1, args.runs + 1):
                run_id = f"{args.run_id}-{candidate}-{model}-r{run_number}"
                target = _output_for_run(output_path, candidate, model, run_number, combinations * args.runs)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("w", newline="", encoding="utf-8") as output:
                    writer = csv.DictWriter(output, fieldnames=fields)
                    writer.writeheader()
                    for row_number, row in enumerate(entries, start=2):
                        audio_path = Path(row["audio_path"])
                        if not audio_path.is_absolute():
                            audio_path = input_path.parent / audio_path
                        if not audio_path.is_file():
                            parser.error(f"Manifest row {row_number} audio does not exist: {audio_path}")
                        result = client.transcribe(
                            audio_path, row["language"], model=model, fmt=args.format,
                            run_id=run_id, use_cache=True,
                        )
                        score = score_utterance(row.get("reference", ""), result.text) if (
                            result.ok and row.get("reference", "").strip()
                        ) else None
                        processing_parameters = row.get("processing_parameters", "")
                        if isinstance(processing_parameters, str):
                            try:
                                processing_parameters = json.loads(processing_parameters)
                            except json.JSONDecodeError:
                                processing_parameters = {"manifest_value": processing_parameters}
                        alignment = score.word if score else None
                        writer.writerow({
                            "id": row.get("id", ""),
                            "source_clip_id": row.get("source_clip_id", ""),
                            "source_type": row.get("source_type", ""),
                            "group": row.get("group", ""),
                            "split": args.split or row.get("split", ""),
                            "language": row.get("language", ""),
                            "speaker_id": row.get("speaker_id", ""),
                            "noise_type": row.get("noise_type", ""),
                            "target_snr_db": row.get("target_snr_db", ""),
                            "model": result.model or (f"mock-prisma-{model}" if mode == "mock" else f"prisma-{model}"),
                            "format": args.format,
                            "sample_rate": 8000,
                            "seed": args.seed,
                            "run": run_id, "candidate": row.get("candidate") or candidate,
                            "enhancement_candidate": row.get("enhancement_candidate") or candidate,
                            "oa_lambda": row.get("oa_lambda", ""),
                            "processing_parameters": json.dumps(processing_parameters, ensure_ascii=False, sort_keys=True),
                            "reference": row.get("reference", ""),
                            "hypothesis": result.text if result.ok else "",
                            "error": result.error or "",
                            "empty_transcript": bool(result.ok and not result.text.strip()),
                            "truncated": result.truncated,
                            "detected_language_code": result.detected_language_code or "",
                            "wer": score.word.errors / score.word.n if score and score.word.n else "",
                            "cer": score.char_errors / score.char_n if score and score.char_n else "",
                            "substitutions": alignment.sub if alignment else "",
                            "deletions": alignment.dele if alignment else "",
                            "insertions": alignment.ins if alignment else "",
                            "word_count": alignment.n if alignment else "",
                            "latency_ms": result.wall_seconds * 1000 if result.wall_seconds is not None else "",
                            "duration_seconds": sf.info(audio_path).duration,
                            "api_call": not result.cached,
                            "cache_hit": result.cached,
                        })
                print(f"Wrote {len(entries)} measured rows to {target}")


if __name__ == "__main__":
    main()
