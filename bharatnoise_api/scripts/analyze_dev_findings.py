"""Write a development-only findings report; never read held-out/test results."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from .. import compare
    from ..metrics import score_utterance
    from .aggregate_results import (
        _failure_label, _snr_bin, _snr_value, MIN_STRATUM_SOURCE_CLIPS, SNR_BIN_WIDTH_DB,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import compare
    from metrics import score_utterance
    from scripts.aggregate_results import (
        _failure_label, _snr_bin, _snr_value, MIN_STRATUM_SOURCE_CLIPS, SNR_BIN_WIDTH_DB,
    )

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _reject_mock(rows: list[dict[str, str]], description: str) -> None:
    if any(row.get("model", "").startswith("mock-") or row.get("hypothesis", "").lstrip().startswith("[MOCK]") for row in rows):
        raise ValueError(f"{description} contains mock outputs and cannot support accuracy findings.")


def _source_id(row: dict[str, str]) -> str:
    return row.get("source_clip_id", "").strip() or row.get("id", "").strip()


def _repeat_noise_floor(path_a: Path, path_b: Path) -> tuple[float | None, int]:
    rows_a, rows_b = _read(path_a), _read(path_b)
    _reject_mock(rows_a + rows_b, "Repeat results")
    pairs, _ = compare.paired_rows(rows_a, rows_b)
    by_source: dict[str, list[list[int]]] = defaultdict(lambda: [[0, 0], [0, 0]])
    for left, right in pairs:
        source_id = _source_id(left)
        if not source_id:
            continue
        for side, row in enumerate((left, right)):
            score = score_utterance(row.get("reference", ""), row.get("hypothesis", ""))
            by_source[source_id][side][0] += score.word.errors
            by_source[source_id][side][1] += score.word.n
    differences = [
        abs(left[0] / left[1] - right[0] / right[1])
        for left, right in by_source.values()
        if left[1] and right[1]
    ]
    return (sum(differences) / len(differences) if differences else None, len(differences))


def _summary_row(
    candidate: str,
    dimension: str,
    group: str,
    pairs: list[tuple[dict[str, str], dict[str, str]]],
    repeat_noise: float | None,
    repeat_sources: int,
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    summary = compare.summarise(pairs, n_boot=n_boot, seed=seed)
    wer = summary.get("wer") or {}
    ci = wer.get("ci95") or [None, None]
    delta = wer.get("delta")
    if summary.get("underpowered") or repeat_sources < MIN_STRATUM_SOURCE_CLIPS:
        status = "UNDERPOWERED"
    elif (
        delta is None or ci[0] is None or ci[1] is None or repeat_noise is None
        or repeat_sources < MIN_STRATUM_SOURCE_CLIPS
    ):
        status = "NO CLAIM: missing paired evidence"
    elif delta < -repeat_noise and ci[1] < 0:
        status = "BEATS C0 beyond repeat noise"
    else:
        status = "NO CLAIM: criterion not met"
    return {
        "candidate": candidate,
        "dimension": dimension,
        "group": group,
        "clips": summary.get("clips", 0),
        "n_source_clips": summary.get("n_source_clips", 0),
        "underpowered": summary.get("underpowered", True),
        "wer_c0": wer.get("A"),
        "wer_candidate": wer.get("B"),
        "delta_wer_candidate_minus_c0": delta,
        "median_per_clip_delta_wer": summary.get("median_delta_wer"),
        "cluster_ci95_low": ci[0],
        "cluster_ci95_high": ci[1],
        "repeat_noise_floor_wer": repeat_noise,
        "repeat_noise_floor_source_clips": repeat_sources,
        "B_better": summary.get("B_better"),
        "tie": summary.get("tie"),
        "A_better": summary.get("A_better"),
        "status": status,
    }


def _pairs_by_dimension(
    pairs: list[tuple[dict[str, str], dict[str, str]]],
) -> dict[tuple[str, str], list[tuple[dict[str, str], dict[str, str]]]]:
    grouped: dict[tuple[str, str], list[tuple[dict[str, str], dict[str, str]]]] = defaultdict(list)
    dimensions = (
        ("language", lambda row: row.get("language", "").strip() or "(unspecified)"),
        ("noise_type", lambda row: row.get("noise_type", "").strip() or "(unspecified)"),
        ("source_type", lambda row: row.get("source_type", "").strip() or "(unspecified)"),
        ("snr_bin", lambda row: (
            f"{_snr_bin(value):g} to <{_snr_bin(value) + SNR_BIN_WIDTH_DB:g} dB"
            if (value := _snr_value(row)) is not None else "(unspecified)"
        )),
    )
    for pair in pairs:
        for dimension, key in dimensions:
            grouped[(dimension, key(pair[0]))].append(pair)
    return grouped


def generate(
    candidates: list[tuple[str, Path]], repeat_a: Path, repeat_b: Path,
    output: Path, n_boot: int = 2000, seed: int = 0,
) -> None:
    if not 200 <= n_boot <= 5000:
        raise ValueError("n_boot must be between 200 and 5000.")
    if len({name for name, _ in candidates}) != len(candidates) or "C0" not in {name for name, _ in candidates}:
        raise ValueError("Provide unique candidate files including C0.")
    loaded = {name: _read(path) for name, path in candidates}
    for name, rows in loaded.items():
        _reject_mock(rows, f"{name} results")
        splits = {row.get("split", "").strip().lower() for row in rows if row.get("split", "").strip()}
        if splits - {"dev", "development"}:
            raise ValueError(f"{name} results contain non-development rows.")
    repeat_noise, repeat_sources = _repeat_noise_floor(repeat_a, repeat_b)
    results = []
    baseline = loaded["C0"]
    for name, rows in loaded.items():
        if name == "C0":
            continue
        pairs, _ = compare.paired_rows(baseline, rows)
        results.append(_summary_row(
            name, "overall", "all", pairs, repeat_noise, repeat_sources, n_boot, seed
        ))
        for (dimension, group), grouped_pairs in sorted(_pairs_by_dimension(pairs).items()):
            results.append(_summary_row(
                name, dimension, group, grouped_pairs, repeat_noise, repeat_sources, n_boot, seed
            ))
    config = json.loads((ROOT / "config" / "diagnosis.json").read_text(encoding="utf-8"))
    threshold = float(config["too_short_words_per_second_threshold"])
    labels = {
        name: Counter(_failure_label(row, threshold) for row in rows)
        for name, rows in loaded.items()
    }
    lines = [
        "# Development findings",
        "",
        "Generated from measured development CSVs only. No test/held-out rows were read.",
        f"Repeat-noise estimate: {repeat_noise if repeat_noise is not None else 'unavailable'} WER; "
        f"{repeat_sources} independent source clips. Failure-label threshold fitted: "
        f"{str(config.get('fitted') is True).lower()}.",
        "",
        "A candidate is called better only when candidate-minus-C0 WER is lower than the negative repeat-noise floor "
        "and its source-cluster bootstrap 95% interval is entirely below zero. Strata below "
        f"{MIN_STRATUM_SOURCE_CLIPS} source clips are UNDERPOWERED and have no comparative claim.",
        "",
        "| Candidate | Dimension | Group | Clips | n_source_clips | C0 WER | Candidate WER | Delta WER | Median per-clip delta | Cluster CI95 | Repeat noise (sources) | Win/tie/loss | Status |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|---|",
    ]
    for row in results:
        ci = "—" if row["cluster_ci95_low"] is None else f"[{row['cluster_ci95_low']:.4f}, {row['cluster_ci95_high']:.4f}]"
        wtl = "—" if row["B_better"] is None else f"{row['B_better']}/{row['tie']}/{row['A_better']}"
        value = lambda key: "—" if row[key] is None else f"{row[key]:.4f}"
        lines.append(
            f"| {row['candidate']} | {row['dimension']} | {row['group']} | {row['clips']} | "
            f"{row['n_source_clips']} | {value('wer_c0')} | {value('wer_candidate')} | "
            f"{value('delta_wer_candidate_minus_c0')} | {value('median_per_clip_delta_wer')} | {ci} | "
            f"{value('repeat_noise_floor_wer')} ({row['repeat_noise_floor_source_clips']}) | {wtl} | {row['status']} |"
        )
    lines.extend(["", "## Failure-label distribution", ""])
    lines.append("| Candidate | " + " | ".join(("ERROR", "EMPTY", "TRUNCATED", "TOO_SHORT", "LANGUAGE_MISMATCH", "WRONG_WORDS", "TEXT_RETURNED")) + " |")
    lines.append("|---|" + "---:|" * 7)
    for candidate, counts in sorted(labels.items()):
        lines.append("| " + candidate + " | " + " | ".join(str(counts[key]) for key in (
            "ERROR", "EMPTY", "TRUNCATED", "TOO_SHORT", "LANGUAGE_MISMATCH", "WRONG_WORDS", "TEXT_RETURNED"
        )) + " |")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", required=True, metavar="NAME=CSV")
    parser.add_argument("--repeat-a", type=Path, required=True)
    parser.add_argument("--repeat-b", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT.parent / "docs" / "dev-findings.md")
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    candidates = []
    try:
        for spec in args.candidate:
            name, path = spec.split("=", 1)
            candidates.append((name, Path(path)))
        generate(candidates, args.repeat_a, args.repeat_b, args.output, args.n_boot, args.seed)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"DEV FINDINGS ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Development-only findings written to {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
