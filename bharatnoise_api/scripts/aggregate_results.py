"""Aggregate measured benchmark CSVs into paired comparison and latency tables."""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from .. import compare
    from ..metrics import normalize, score_utterance
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import compare
    from metrics import normalize, score_utterance


_AGGREGATES = {
    "final_results.csv", "by_noise.csv", "by_snr.csv", "by_language.csv", "by_source_type.csv",
    "ablation_results.csv", "latency.csv",
}
MIN_STRATUM_SOURCE_CLIPS = 20
SNR_BIN_WIDTH_DB = 5.0
FAILURE_LABELS = (
    "ERROR", "EMPTY", "TRUNCATED", "TOO_SHORT", "LANGUAGE_MISMATCH",
    "WRONG_WORDS", "TEXT_RETURNED",
)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _candidate(path: Path, rows: list[dict[str, str]]) -> str:
    labels = {row.get("candidate", "") for row in rows if row.get("candidate")}
    return next(iter(labels)) if len(labels) == 1 else path.stem


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _truth(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _failure_label(row: dict[str, str], too_short_threshold: float) -> str:
    if row.get("error"):
        return "ERROR"
    hypothesis = row.get("hypothesis", "")
    if not hypothesis.strip():
        return "EMPTY"
    if _truth(row.get("truncated")):
        return "TRUNCATED"
    words = normalize(hypothesis).split()
    try:
        duration = float(row.get("duration_seconds", ""))
    except (TypeError, ValueError):
        duration = 0.0
    if len(words) < 2 or (duration > 0 and len(words) / duration < too_short_threshold):
        return "TOO_SHORT"
    requested = row.get("language", "").lower().replace("_", "-")
    detected = row.get("detected_language_code", "").lower().replace("_", "-")
    if requested and requested != "indic-auto" and detected:
        if requested.split("-")[0] != detected.split("-")[0]:
            return "LANGUAGE_MISMATCH"
    reference = row.get("reference", "")
    if normalize(reference):
        score = score_utterance(reference, hypothesis)
        if score.word.errors:
            return "WRONG_WORDS"
    return "TEXT_RETURNED"


def _diagnostics(rows: list[dict[str, str]], too_short_threshold: float) -> dict[str, Any]:
    failed = sum(bool(row.get("error")) for row in rows)
    empty = sum(
        _truth(row.get("empty_transcript"))
        or (not row.get("error") and not row.get("hypothesis", "").strip())
        for row in rows
    )
    truncated = sum(_truth(row.get("truncated")) for row in rows)
    api_calls = sum(_truth(row.get("api_call")) for row in rows) if rows and "api_call" in rows[0] else None
    labels = Counter(_failure_label(row, too_short_threshold) for row in rows)
    count = len(rows)
    return {
        "transcript_rows": count,
        "failed_requests": failed,
        "empty_transcripts": empty,
        "truncated_responses": truncated,
        "failure_rate": failed / count if count else None,
        "api_calls": api_calls,
        "diagnosis_thresholds_fitted": False,
        **{f"label_{label.lower()}": labels[label] for label in FAILURE_LABELS},
    }


def _noise_group(row: dict[str, str]) -> str:
    value = row.get("noise_type", "").strip()
    if value:
        return value
    group = row.get("group", "").strip()
    return re.sub(r"_snr-?\d+(?:\.\d+)?$", "", group) or "(none)"


def _snr_value(row: dict[str, str]) -> float | None:
    value = _num(row.get("target_snr_db"))
    if value is not None:
        return value
    match = re.search(r"_snr(-?\d+(?:\.\d+)?)", row.get("group", ""))
    return float(match.group(1)) if match else None


def _snr_bin(value: float) -> float:
    return math.floor(value / SNR_BIN_WIDTH_DB) * SNR_BIN_WIDTH_DB


def _write(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _paired_summary(
    candidate: str,
    model: str,
    group: str,
    pairs: list[tuple[dict, dict]],
    n_boot: int,
    seed: int,
    too_short_threshold: float,
    candidate_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    summary = compare.summarise(pairs, n_boot, seed)
    wer = summary.get("wer") or {}
    cer = summary.get("cer") or {}
    wer_ci = wer.get("ci95") or [None, None]
    cer_ci = cer.get("ci95") or [None, None]
    measured_rows = candidate_rows if candidate_rows is not None else [pair[1] for pair in pairs]
    paired_rows = [right for _, right in pairs]
    source_clip_ids = {
        row.get("source_clip_id", "").strip() or row.get("id", "").strip()
        for row in paired_rows
        if row.get("source_clip_id", "").strip() or row.get("id", "").strip()
    }
    speaker_ids = {row.get("speaker_id", "").strip() for row in paired_rows if row.get("speaker_id", "").strip()}
    source_types = {row.get("source_type", "").strip() for row in measured_rows if row.get("source_type", "").strip()}
    underpowered = len(source_clip_ids) < MIN_STRATUM_SOURCE_CLIPS
    per_clip_deltas = []
    for left, right in pairs:
        left_score = score_utterance(left.get("reference", ""), left.get("hypothesis", ""))
        right_score = score_utterance(right.get("reference", ""), right.get("hypothesis", ""))
        if left_score.word.n and right_score.word.n:
            per_clip_deltas.append(
                right_score.word.errors / right_score.word.n
                - left_score.word.errors / left_score.word.n
            )
    latencies = [value for row in measured_rows if (value := _num(row.get("latency_ms"))) is not None]
    return {
        "candidate": candidate, "model": model, "group": group,
        "source_type": next(iter(source_types)) if len(source_types) == 1 else "mixed" if source_types else "",
        "split": next(
            (row.get("split", "") for row in (candidate_rows or []) if row.get("split", "")),
            "",
        ),
        "clips": summary.get("clips", 0),
        "n_source_clips": len(source_clip_ids),
        "n_speakers": len(speaker_ids),
        "underpowered": underpowered,
        "wer": wer.get("B"), "cer": cer.get("B"),
        "delta_wer": None if underpowered else wer.get("delta"),
        "median_delta_wer": None if underpowered or not per_clip_deltas else statistics.median(per_clip_deltas),
        "delta_cer": None if underpowered else cer.get("delta"),
        "wer_ci95_low": None if underpowered else wer_ci[0],
        "wer_ci95_high": None if underpowered else wer_ci[1],
        "cer_ci95_low": None if underpowered else cer_ci[0],
        "cer_ci95_high": None if underpowered else cer_ci[1],
        "B_better": None if underpowered else summary.get("B_better"),
        "tie": None if underpowered else summary.get("tie"),
        "A_better": None if underpowered else summary.get("A_better"),
        "latency_p50_ms": statistics.median(latencies) if latencies else None,
        "latency_p95_ms": _percentile(latencies, 0.95) if latencies else None,
        **_diagnostics(measured_rows, too_short_threshold),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path(__file__).resolve().parents[1] / "results")
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if not 200 <= args.n_boot <= 5000:
        parser.error("--n-boot must be between 200 and 5000.")
    try:
        diagnosis_config = json.loads(
            (Path(__file__).resolve().parents[1] / "config" / "diagnosis.json").read_text(encoding="utf-8")
        )
        too_short_threshold = float(diagnosis_config["too_short_words_per_second_threshold"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"Cannot load the explicit diagnosis threshold: {type(exc).__name__}.")
    if diagnosis_config.get("fitted") is not False:
        parser.error("Diagnosis failure-label thresholds must remain explicitly unfitted.")
    source_files = sorted(
        path for path in args.results_dir.glob("*.csv")
        if path.name not in _AGGREGATES and not path.name.endswith(".tmp.csv")
    )
    if not source_files:
        parser.error(
            f"No benchmark CSV files found in {args.results_dir}. Run run_baseline.py against the official "
            "Gnani endpoint before aggregating measured results."
        )
    datasets = [(path, _read(path)) for path in source_files]
    if any(
        row.get("model", "").startswith("mock-")
        or row.get("hypothesis", "").lstrip().startswith("[MOCK]")
        for _, rows in datasets for row in rows
    ):
        parser.error("Mock transcripts are plumbing placeholders and cannot be aggregated as benchmark results.")
    split_names = {
        row.get("split", "").strip().lower()
        for _, rows in datasets for row in rows if row.get("split", "").strip()
    }
    if len(split_names) > 1:
        parser.error("Development and held-out rows must be aggregated separately.")
    labeled = [(_candidate(path, rows), rows) for path, rows in datasets]
    candidates: dict[str, list[dict[str, str]]] = {}
    for name, rows in labeled:
        candidates.setdefault(name, []).extend(rows)
    for candidate, rows in candidates.items():
        seen: set[tuple[str, str]] = set()
        for row in rows:
            identity = (row.get("model", ""), row.get("id", ""))
            if identity in seen:
                parser.error(
                    f"Candidate {candidate} contains repeated clip/model rows; "
                    "aggregate one run per candidate or compare repeat runs separately."
                )
            seen.add(identity)
    if "C0" not in candidates:
        parser.error("Include a result CSV labeled C0 to calculate paired deltas.")
    final_rows: list[dict[str, Any]] = []
    noise_rows: list[dict[str, Any]] = []
    snr_rows: list[dict[str, Any]] = []
    language_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    ablation_rows: list[dict[str, Any]] = []
    latency_rows: list[dict[str, Any]] = []
    baseline = candidates["C0"]
    for candidate, rows in sorted(candidates.items()):
        grouped_models = sorted({row.get("model", "") for row in rows} | {row.get("model", "") for row in baseline})
        for model in grouped_models:
            if not model:
                continue
            left = [r for r in baseline if r.get("model", "") == model]
            right = [r for r in rows if r.get("model", "") == model]
            pairs, _ = compare.paired_rows(left, right)
            candidate_model_rows = [row for row in rows if row.get("model", "") == model]
            if pairs or candidate_model_rows:
                summary = _paired_summary(
                    candidate, model, "overall", pairs, args.n_boot, args.seed,
                    too_short_threshold, candidate_model_rows
                )
                final_rows.append(summary)
                ablation_rows.append({k: summary[k] for k in (
                    "candidate", "model", "split", "clips", "wer", "cer", "delta_wer", "delta_cer",
                    "wer_ci95_low", "wer_ci95_high", "cer_ci95_low", "cer_ci95_high",
                    "B_better", "tie", "A_better",
                    "latency_p50_ms", "latency_p95_ms",
                    "transcript_rows", "failed_requests", "empty_transcripts",
                    "truncated_responses", "failure_rate", "api_calls", "n_source_clips",
                    "n_speakers", "underpowered", "source_type", "median_delta_wer",
                    "diagnosis_thresholds_fitted",
                    *(f"label_{label.lower()}" for label in FAILURE_LABELS),
                )})
                for field, destination, key_for_row in (
                    ("noise_type", noise_rows, _noise_group),
                    ("language", language_rows, lambda row: row.get("language") or "(none)"),
                    ("source_type", source_rows, lambda row: row.get("source_type") or "(none)"),
                ):
                    values: dict[str, list[tuple[dict, dict]]] = {}
                    for pair in pairs:
                        key = key_for_row(pair[0])
                        values.setdefault(key, []).append(pair)
                    for row in candidate_model_rows:
                        key = key_for_row(row)
                        values.setdefault(key, [])
                    for group, group_pairs in sorted(values.items()):
                        group_rows = [
                            row for row in candidate_model_rows
                            if key_for_row(row) == group
                        ]
                        row_summary = _paired_summary(
                            candidate, model, group, group_pairs, args.n_boot, args.seed,
                            too_short_threshold, group_rows
                        )
                        destination.append(row_summary)
                snr_groups: dict[float, list[tuple[dict, dict]]] = {}
                for pair in pairs:
                    value = _snr_value(pair[0])
                    if value is not None:
                        snr_groups.setdefault(_snr_bin(value), []).append(pair)
                for row in candidate_model_rows:
                    value = _snr_value(row)
                    if value is not None:
                        snr_groups.setdefault(_snr_bin(value), [])
                for value, snr_pairs in sorted(snr_groups.items()):
                    value_rows = [
                        row for row in candidate_model_rows
                        if _snr_value(row) is not None and _snr_bin(_snr_value(row)) == value
                    ]
                    row_summary = _paired_summary(
                        candidate, model, f"{value:g} to <{value + SNR_BIN_WIDTH_DB:g} dB",
                        snr_pairs, args.n_boot, args.seed,
                        too_short_threshold, value_rows
                    )
                    snr_rows.append({
                        **row_summary, "snr_db": value,
                        "snr_bin": f"{value:g} to <{value + SNR_BIN_WIDTH_DB:g} dB",
                    })
        for model in sorted({row.get("model", "") for row in rows}):
            latencies = [
                value for row in rows if row.get("model", "") == model
                if (value := _num(row.get("latency_ms"))) is not None
            ]
            if latencies:
                latency_rows.append({
                    "candidate": candidate, "model": model, "clips": len(latencies),
                    "p50_ms": statistics.median(latencies), "p95_ms": _percentile(latencies, 0.95),
                    "api_calls": _diagnostics(
                        [row for row in rows if row.get("model", "") == model], too_short_threshold
                    )["api_calls"],
                })
    fields = ["candidate", "model", "group", "source_type", "split", "clips", "n_source_clips",
              "n_speakers", "underpowered", "wer", "cer", "delta_wer", "delta_cer",
              "median_delta_wer",
              "wer_ci95_low", "wer_ci95_high", "cer_ci95_low", "cer_ci95_high",
              "B_better", "tie", "A_better",
              "latency_p50_ms", "latency_p95_ms", "transcript_rows", "failed_requests",
              "empty_transcripts", "truncated_responses", "failure_rate"]
    fields.extend(["api_calls", "diagnosis_thresholds_fitted"])
    fields.extend(f"label_{label.lower()}" for label in FAILURE_LABELS)
    args.results_dir.mkdir(parents=True, exist_ok=True)
    _write(args.results_dir / "final_results.csv", final_rows, fields)
    _write(args.results_dir / "by_noise.csv", noise_rows, fields)
    _write(args.results_dir / "by_snr.csv", snr_rows, fields + ["snr_db", "snr_bin"])
    _write(args.results_dir / "by_language.csv", language_rows, fields)
    _write(args.results_dir / "by_source_type.csv", source_rows, fields)
    _write(args.results_dir / "ablation_results.csv", ablation_rows, fields)
    _write(args.results_dir / "latency.csv", latency_rows, ["candidate", "model", "clips", "p50_ms", "p95_ms", "api_calls"])


if __name__ == "__main__":
    main()
