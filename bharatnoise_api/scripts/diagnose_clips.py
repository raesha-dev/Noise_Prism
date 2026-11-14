"""Run C0-only Prisma diagnostics; live requests require an explicit budget confirmation."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import soundfile as sf

try:
    from ..gnani_client import FatalAPIError, GnaniClient, Transcript, endpoint_mode
    from ..metrics import normalize, score_utterance
    from .plan_budget import project_path
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from gnani_client import FatalAPIError, GnaniClient, Transcript, endpoint_mode
    from metrics import normalize, score_utterance
    from scripts.plan_budget import project_path

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = {
    "v25-correct": ("v2.5", "manifest", "verbatim"),
    "v30-correct": ("v3.0", "manifest", "verbatim"),
    "v30-auto": ("v3.0", "indic-auto", "verbatim"),
}
OUTPUT_FIELDS = [
    "id", "source_clip_id", "source_type", "split", "group", "condition",
    "model", "format", "language_requested", "language_detected", "duration_seconds",
    "empty", "truncated", "word_count", "words_per_second", "failure_label",
    "wer", "cer", "diagnosis_thresholds_fitted", "api_call", "cache_hit", "error",
]


class Transcriber(Protocol):
    def transcribe(
        self, path: str | Path, language: str, *, model: str, fmt: str | None,
        run_id: str,
    ) -> Transcript: ...


def load_manifest(path: Path, limit: int | None = None) -> list[dict[str, str]]:
    manifest = project_path(path).resolve()
    if not manifest.is_file():
        raise ValueError(f"Manifest does not exist: {manifest}")
    with manifest.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"id", "audio_path", "language"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Manifest is missing required columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be at least one.")
        rows = rows[:limit]
    if not rows:
        raise ValueError("Manifest selection contains no clips.")
    for row_number, row in enumerate(rows, start=2):
        source = Path(row["audio_path"])
        if not source.is_absolute():
            source = manifest.parent / source
        row["_audio_path"] = str(source.resolve())
        try:
            duration = float(sf.info(source).duration)
        except (OSError, RuntimeError, ValueError):
            raise ValueError(f"Could not inspect manifest row {row_number} audio.") from None
        if duration <= 0 or duration > 30:
            raise ValueError(f"Manifest row {row_number} is outside the 0-30 second REST limit.")
        row["_duration_seconds"] = str(duration)
        if not row.get("language", "").strip():
            raise ValueError(f"Manifest row {row_number} has no reference language.")
    return rows


def classify_result(
    result: Transcript, duration_seconds: float, words_per_second_threshold: float,
) -> tuple[str, int, float]:
    words = len(normalize(result.text).split())
    rate = words / duration_seconds if duration_seconds > 0 else 0.0
    if not result.ok:
        label = "ERROR"
    elif not result.text.strip():
        label = "EMPTY"
    elif result.truncated:
        label = "TRUNCATED"
    elif words < 2 or rate < words_per_second_threshold:
        label = "TOO_SHORT"
    else:
        label = "TEXT_RETURNED"
    return label, words, rate


def diagnose(
    rows: list[dict[str, str]], client: Transcriber, conditions: list[str], run_id: str,
    words_per_second_threshold: float,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for condition in conditions:
        model, language_mode, output_format = CONDITIONS[condition]
        for row in rows:
            duration = float(row["_duration_seconds"])
            language = row["language"].strip() if language_mode == "manifest" else language_mode
            result = client.transcribe(
                row["_audio_path"], language, model=model, fmt=output_format,
                run_id=f"{run_id}:{condition}",
            )
            reference = row.get("reference", "").strip()
            score = score_utterance(reference, result.text) if reference and result.ok else None
            label, words, rate = classify_result(result, duration, words_per_second_threshold)
            detected = (result.detected_language_code or "").lower().replace("_", "-")
            requested = language.lower().replace("_", "-")
            if label == "TEXT_RETURNED" and requested != "indic-auto" and detected:
                if detected.split("-")[0] != requested.split("-")[0]:
                    label = "LANGUAGE_MISMATCH"
            if label == "TEXT_RETURNED" and score and score.word.n and score.word.errors:
                label = "WRONG_WORDS"
            results.append({
                "id": row["id"],
                "source_clip_id": row.get("source_clip_id", ""),
                "source_type": row.get("source_type", ""),
                "split": row.get("split", ""),
                "group": row.get("group", ""),
                "condition": condition,
                "model": model,
                "format": output_format,
                "language_requested": language,
                "language_detected": result.detected_language_code or "",
                "duration_seconds": f"{duration:.3f}",
                "empty": str(result.ok and not result.text.strip()).lower(),
                "truncated": str(result.truncated).lower(),
                "word_count": words,
                "words_per_second": f"{rate:.4f}",
                "failure_label": label,
                "wer": f"{score.word.errors / score.word.n:.6f}" if score and score.word.n else "",
                "cer": f"{score.char_errors / score.char_n:.6f}" if score and score.char_n else "",
                "diagnosis_thresholds_fitted": "false",
                "api_call": str(not result.cached).lower(),
                "cache_hit": str(result.cached).lower(),
                "error": result.error or "",
            })
    return results


def write_results(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_condition_results(
    output_prefix: Path, rows: list[dict[str, object]],
) -> dict[str, Path]:
    """Keep each model/language condition in its own local result file."""
    outputs: dict[str, Path] = {}
    for condition in dict.fromkeys(str(row["condition"]) for row in rows):
        destination = output_prefix.with_name(
            f"{output_prefix.stem}_{condition}{output_prefix.suffix or '.csv'}"
        )
        write_results(destination, [row for row in rows if row["condition"] == condition])
        outputs[condition] = destination
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--conditions", nargs="+", choices=sorted(CONDITIONS), default=list(CONDITIONS))
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("diagnosis-%Y%m%dT%H%M%SZ"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--budget-remaining", type=int)
    args = parser.parse_args()
    try:
        rows = load_manifest(args.manifest, args.limit)
    except ValueError as exc:
        print(f"DIAGNOSIS ERROR: {exc}", file=sys.stderr)
        return 2
    planned_calls = len(rows) * len(args.conditions)
    planned_minutes = sum(float(row["_duration_seconds"]) for row in rows) * len(args.conditions) / 60
    print(f"C0-only diagnostic clips: {len(rows)}")
    print(f"Conditions: {', '.join(args.conditions)}")
    print(f"Planned maximum calls (cache hits may reduce this): {planned_calls}")
    print(f"Planned audio: {planned_minutes:.3f} minutes")
    if args.dry_run:
        print("Dry run: no API requests were made.")
        return 0
    if not args.confirm_live:
        print("Live run blocked: add --confirm-live after reviewing this plan.", file=sys.stderr)
        return 2
    if args.budget_remaining is None or args.budget_remaining < 0:
        print("Live run blocked: provide a non-negative --budget-remaining.", file=sys.stderr)
        return 2
    if planned_calls > args.budget_remaining:
        print("Live run blocked: planned calls exceed the declared remaining budget.", file=sys.stderr)
        return 2
    if args.output is None:
        print("Live run blocked: --output is required.", file=sys.stderr)
        return 2
    config_path = ROOT / "config" / "diagnosis.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("fitted") is not False:
        print("Live run blocked: diagnosis thresholds must remain explicitly unfitted.", file=sys.stderr)
        return 2
    if endpoint_mode() != "live":
        print("Live run blocked: configure the official Gnani endpoint and API key.", file=sys.stderr)
        return 2
    client = GnaniClient(cache_dir=ROOT / "data" / "cache")
    try:
        results = diagnose(
            rows, client, args.conditions, args.run_id,
            float(config["too_short_words_per_second_threshold"]),
        )
    except FatalAPIError as exc:
        print(f"DIAGNOSIS STOPPED: {exc}", file=sys.stderr)
        return 3
    outputs = write_condition_results(project_path(args.output), results)
    for condition, path in outputs.items():
        print(f"{condition}: {path}; transcript text was not stored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
