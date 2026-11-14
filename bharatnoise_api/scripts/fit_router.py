"""Fit SNR routing on development-only results (minimum ten clips) and measured repeat noise."""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import sys
from pathlib import Path

try:
    from ..metrics import score_utterance
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from metrics import score_utterance

MIN_DEV_CLIPS = 10


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _errors(row: dict[str, str]) -> int:
    return score_utterance(row.get("reference", ""), row.get("hypothesis", "")).word.errors


def _contains_mock(rows: list[dict[str, str]]) -> bool:
    return any(
        row.get("model", "").startswith("mock-")
        or row.get("hypothesis", "").lstrip().startswith("[MOCK]")
        for row in rows
    )


def _validate_development_split(rows: list[dict[str, str]], description: str) -> bool:
    labels = {row.get("split", "").strip().lower() for row in rows if row.get("split", "").strip()}
    if labels & {"held-out", "heldout", "test"}:
        raise ValueError(f"{description} contains held-out/test rows; router fitting is development-only.")
    if labels - {"development", "dev"}:
        raise ValueError(f"{description} has an unsupported split label: {', '.join(sorted(labels))}.")
    return bool(labels)


def _validate_min_dev_clips(count: int) -> None:
    if count < MIN_DEV_CLIPS:
        raise ValueError(f"At least {MIN_DEV_CLIPS} eligible development clips are required; received {count}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", required=True, metavar="CANDIDATE=CSV",
                        help="Dev results; repeat for each candidate including C0")
    parser.add_argument(
        "--snr-csv", type=Path, required=True,
        help="Clip ids and stationary_snr_db_est values",
    )
    parser.add_argument("--repeat-a", type=Path, required=True, help="First same-audio repeat run")
    parser.add_argument("--repeat-b", type=Path, required=True, help="Second same-audio repeat run")
    parser.add_argument("--snr-estimator", choices=("stationary_snr_db_est", "wada_snr_db"),
                        default="stationary_snr_db_est")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "config" / "router.json")
    parser.add_argument("--seed", type=int, default=0, help="Reserved deterministic seed for fitting metadata")
    args = parser.parse_args()
    results: dict[str, dict[str, dict[str, str]]] = {}
    split_verified = True
    for specification in args.candidate:
        try:
            candidate, filename = specification.split("=", 1)
        except ValueError:
            parser.error("--candidate values must be CANDIDATE=CSV.")
        rows = _read(Path(filename))
        try:
            split_verified &= _validate_development_split(rows, f"{candidate} results")
        except ValueError as exc:
            parser.error(str(exc))
        if _contains_mock(rows):
            parser.error("Mock transcripts are plumbing placeholders and cannot be used to fit the router.")
        results[candidate] = {row["id"]: row for row in rows}
    if "C0" not in results:
        parser.error("A C0 baseline is required.")
    snr_config_path = Path(__file__).resolve().parents[1] / "config" / "snr_estimators.json"
    try:
        snr_config = json.loads(snr_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"Cannot read SNR estimator validation state: {type(exc).__name__}.")
    estimator = snr_config.get("estimators", {}).get(args.snr_estimator, {})
    if snr_config.get("routing_enabled") is not True or estimator.get("validated") is not True:
        parser.error("SNR routing is disabled until the selected estimator validates on development mixtures.")
    snr_rows = _read(args.snr_csv)
    snr_by_id: dict[str, float] = {}
    for row in snr_rows:
        value = row.get(args.snr_estimator)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            snr_by_id[row["id"]] = parsed
    shared_ids = set(snr_by_id) & set(results["C0"])
    if not shared_ids:
        parser.error("No clip ids overlap between the SNR estimates and dev baseline.")
    repeat_a = {row["id"]: row for row in _read(args.repeat_a)}
    repeat_b = {row["id"]: row for row in _read(args.repeat_b)}
    try:
        split_verified &= _validate_development_split(list(repeat_a.values()), "First repeat run")
        split_verified &= _validate_development_split(list(repeat_b.values()), "Second repeat run")
    except ValueError as exc:
        parser.error(str(exc))
    if _contains_mock(list(repeat_a.values()) + list(repeat_b.values())):
        parser.error("Mock transcripts are plumbing placeholders and cannot be used to fit the router.")
    repeat_ids = set(repeat_a) & set(repeat_b)
    if not repeat_ids:
        parser.error("The two repeat-run CSVs contain no shared clip ids.")
    noise_floor = sum(abs(_errors(repeat_a[key]) - _errors(repeat_b[key])) for key in repeat_ids)

    variants = sorted(set(results) & {"C0", "C1", "C2", "C3"})
    eligible = {
        clip_id: {
            candidate: results[candidate][clip_id]
            for candidate in variants
            if clip_id in results[candidate]
            and not results[candidate][clip_id].get("error")
            and results[candidate][clip_id].get("reference")
        }
        for clip_id in shared_ids
    }
    eligible = {key: value for key, value in eligible.items() if "C0" in value}
    if not eligible:
        parser.error("No successful, referenced dev rows are common with C0.")
    try:
        _validate_min_dev_clips(len(eligible))
    except ValueError as exc:
        parser.error(str(exc))
    baseline_errors = sum(_errors(rows["C0"]) for rows in eligible.values())
    sorted_snr = sorted(snr_by_id[key] for key in eligible)
    thresholds = sorted({sorted_snr[round(q * (len(sorted_snr) - 1))] for q in (0.25, 0.5, 0.75)})
    choices = list(itertools.product(variants, repeat=3))
    best: tuple[int, float, float, tuple[str, str, str]] | None = None
    for low, high in itertools.combinations_with_replacement(thresholds, 2):
        for assignment in choices:
            total = 0
            for clip_id, rows in eligible.items():
                snr = snr_by_id[clip_id]
                slot = 0 if snr >= high else 1 if snr >= low else 2
                chosen = assignment[slot]
                total += _errors(rows.get(chosen, rows["C0"]))
            candidate_result = (total, low, high, assignment)
            if best is None or candidate_result < best:
                best = candidate_result
    if best is None:
        parser.error("The router grid search had no candidate assignments.")
    best_errors, low, high, assignment = best
    improvement = baseline_errors - best_errors
    accepted = improvement > noise_floor
    if accepted:
        rules = [
            {"min_snr_db": high, "variant": assignment[0]},
            {"min_snr_db": low, "variant": assignment[1]},
            {"min_snr_db": -100, "variant": assignment[2]},
        ]
    else:
        rules = [{"min_snr_db": -100, "variant": "C0"}]
    fitted = {
        "fitted": True,
        "routing_estimator_validated": True,
        "routing_estimator": args.snr_estimator,
        "note": (
            "Fitted using development rows only; test rows were not read. No candidate beat the original "
            "by more than the measured repeat-run noise floor; all SNR ranges route to C0."
            if not accepted else "Fitted using development rows only; test rows were not read."
        ) + ("" if split_verified else " Split labels were absent, so development provenance was not verified."),
        "rules": rules,
        "fallback_chain": ["C2", "C1", "C0"],
        "guard": {"max_lsd_db": 6.0, "max_speech_energy_change_db": 12.0},
        "dev_statistics": {
            "clips": len(eligible), "baseline_word_errors": baseline_errors,
            "routed_word_errors": best_errors, "improvement_word_errors": improvement,
            "repeat_run_noise_floor_word_errors": noise_floor,
            "selected_rules_beat_noise_floor": accepted,
            "split_verified": split_verified,
            "seed": args.seed,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fitted, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
