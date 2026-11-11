"""Aggregate measured benchmark CSVs into paired comparison and latency tables."""
from __future__ import annotations

import argparse
import csv
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Any

try:
    from .. import compare
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import compare


_AGGREGATES = {
    "final_results.csv", "by_noise.csv", "by_snr.csv", "by_language.csv",
    "ablation_results.csv", "latency.csv",
}


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


def _write(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _paired_summary(candidate: str, model: str, group: str, pairs: list[tuple[dict, dict]], n_boot: int, seed: int) -> dict[str, Any]:
    summary = compare.summarise(pairs, n_boot, seed)
    wer = summary.get("wer") or {}
    cer = summary.get("cer") or {}
    wer_ci = wer.get("ci95") or [None, None]
    cer_ci = cer.get("ci95") or [None, None]
    latencies = [value for pair in pairs if (value := _num(pair[1].get("latency_ms"))) is not None]
    return {
        "candidate": candidate, "model": model, "group": group, "clips": summary.get("clips", 0),
        "wer": wer.get("B"), "cer": cer.get("B"), "delta_wer": wer.get("delta"),
        "delta_cer": cer.get("delta"),
        "wer_ci95_low": wer_ci[0], "wer_ci95_high": wer_ci[1],
        "cer_ci95_low": cer_ci[0], "cer_ci95_high": cer_ci[1],
        "B_better": summary.get("B_better"), "tie": summary.get("tie"),
        "A_better": summary.get("A_better"),
        "latency_p50_ms": statistics.median(latencies) if latencies else None,
        "latency_p95_ms": _percentile(latencies, 0.95) if latencies else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path(__file__).resolve().parents[1] / "results")
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if not 200 <= args.n_boot <= 5000:
        parser.error("--n-boot must be between 200 and 5000.")
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
    labeled = [(_candidate(path, rows), rows) for path, rows in datasets]
    candidates: dict[str, list[dict[str, str]]] = {}
    for name, rows in labeled:
        candidates.setdefault(name, []).extend(rows)
    if "C0" not in candidates:
        parser.error("Include a result CSV labeled C0 to calculate paired deltas.")
    final_rows: list[dict[str, Any]] = []
    noise_rows: list[dict[str, Any]] = []
    snr_rows: list[dict[str, Any]] = []
    language_rows: list[dict[str, Any]] = []
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
            if pairs:
                summary = _paired_summary(candidate, model, "overall", pairs, args.n_boot, args.seed)
                final_rows.append(summary)
                ablation_rows.append({k: summary[k] for k in (
                    "candidate", "model", "clips", "wer", "cer", "delta_wer", "delta_cer",
                    "wer_ci95_low", "wer_ci95_high", "cer_ci95_low", "cer_ci95_high",
                    "B_better", "tie", "A_better",
                    "latency_p50_ms", "latency_p95_ms",
                )})
                for field, destination, parsed_group in (
                    ("group", noise_rows, lambda value: value),
                    ("language", language_rows, lambda value: value),
                ):
                    values: dict[str, list[tuple[dict, dict]]] = {}
                    for pair in pairs:
                        key = pair[0].get(field) or "(none)"
                        values.setdefault(parsed_group(key), []).append(pair)
                    for group, group_pairs in sorted(values.items()):
                        row_summary = _paired_summary(candidate, model, group, group_pairs, args.n_boot, args.seed)
                        destination.append(row_summary)
                        if field == "group":
                            match = re.search(r"_snr(-?\d+(?:\.\d+)?)", group)
                            if match:
                                snr_rows.append({**row_summary, "snr_db": float(match.group(1))})
        for model in sorted({row.get("model", "") for row in rows}):
            latencies = [
                value for row in rows if row.get("model", "") == model
                if (value := _num(row.get("latency_ms"))) is not None
            ]
            if latencies:
                latency_rows.append({
                    "candidate": candidate, "model": model, "clips": len(latencies),
                    "p50_ms": statistics.median(latencies), "p95_ms": _percentile(latencies, 0.95),
                })
    fields = ["candidate", "model", "group", "clips", "wer", "cer", "delta_wer", "delta_cer",
              "wer_ci95_low", "wer_ci95_high", "cer_ci95_low", "cer_ci95_high",
              "B_better", "tie", "A_better",
              "latency_p50_ms", "latency_p95_ms"]
    args.results_dir.mkdir(parents=True, exist_ok=True)
    _write(args.results_dir / "final_results.csv", final_rows, fields)
    _write(args.results_dir / "by_noise.csv", noise_rows, fields)
    _write(args.results_dir / "by_snr.csv", snr_rows, fields + ["snr_db"])
    _write(args.results_dir / "by_language.csv", language_rows, fields)
    _write(args.results_dir / "ablation_results.csv", ablation_rows, fields)
    _write(args.results_dir / "latency.csv", latency_rows, ["candidate", "model", "clips", "p50_ms", "p95_ms"])


if __name__ == "__main__":
    main()
