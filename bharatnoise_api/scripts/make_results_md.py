"""Render public result summaries from aggregate CSV files only."""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README_START = "<!-- AUTO-RESULTS:START -->"
README_END = "<!-- AUTO-RESULTS:END -->"
AGGREGATE_FILES = {
    "final_results.csv", "by_noise.csv", "by_snr.csv", "by_language.csv",
    "by_source_type.csv", "ablation_results.csv", "latency.csv",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _number(value: str) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _show(value: str, *, percent: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "not measured"
    return f"{number:.2%}" if percent else f"{number:.1f}"


def _truth(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes"}


def _comparison(row: dict[str, str], field: str, *, percent: bool = False) -> str:
    if _truth(row.get("underpowered", "")):
        return "UNDERPOWERED"
    return _show(row.get(field, ""), percent=percent)


def _calls_per_clip(row: dict[str, str]) -> str:
    calls = _number(row.get("api_calls", ""))
    clips = _number(row.get("transcript_rows", ""))
    return f"{calls / clips:.2f}" if calls is not None and clips else "not measured"


def render_results(results_dir: Path) -> str:
    """Render summary tables, or explicitly say that results are unavailable."""
    summary_path = results_dir / "final_results.csv"
    if not summary_path.is_file():
        raw_files = [
            path for path in results_dir.glob("*.csv")
            if path.name not in AGGREGATE_FILES and not path.name.endswith(".tmp.csv")
        ]
        if raw_files:
            return (
                "# Results\n\n"
                "**Measured result files exist but have not been aggregated.** "
                "WER/CER and comparative summaries are not reported until the "
                "split-specific aggregate is generated.\n\n"
                "Run `aggregate_results.py` on a directory containing one run "
                "per candidate/model, then regenerate this report. Keep mock "
                "transcripts and repeat-run files out of the aggregate.\n"
            )
        return (
            "# Results\n\n"
            "**No measured benchmark results are available.** WER, CER, paired "
            "confidence intervals, latency, and candidate comparisons are not measured.\n\n"
            "Run the budgeted development protocol in [RUNBOOK.md](./RUNBOOK.md), "
            "then aggregate its result CSVs before regenerating this report. Mock "
            "transcripts are not benchmark evidence. The held-out split remains "
            "reserved for the owner's single post-freeze run.\n"
        )
    rows = _read_csv(summary_path)
    if not rows:
        return (
            "# Results\n\n"
            "**No measured benchmark rows are available.** The aggregate CSV is empty.\n"
        )
    lines = [
        "# Results",
        "",
        "Generated from the aggregate `final_results.csv`; no values are manually entered.",
        "",
        "| Candidate | Model | Split | Clips | Independent source clips | Status | WER | Delta vs C0 | 95% CI for delta | Win / tie / loss | Calls / clip | p50 latency | p95 latency |",
        "|---|---|---|---:|---:|---|---:|---:|---|---|---:|---:|---:|",
    ]
    for row in rows:
        underpowered = _truth(row.get("underpowered", ""))
        ci = (
            "UNDERPOWERED"
            if underpowered
            else f"{_show(row.get('wer_ci95_low', ''), percent=True)} to "
            f"{_show(row.get('wer_ci95_high', ''), percent=True)}"
        )
        win_loss = (
            "UNDERPOWERED"
            if underpowered
            else " / ".join(row.get(key, "") or "not measured" for key in ("B_better", "tie", "A_better"))
        )
        lines.append(
            "| {candidate} | {model} | {split} | {clips} | {sources} | {status} | "
            "{wer} | {delta} | {ci} | {win_loss} | {calls} | {p50} | {p95} |".format(
                candidate=row.get("candidate") or "not measured",
                model=row.get("model") or "not measured",
                split=row.get("split") or "not measured",
                clips=row.get("clips") or "not measured",
                sources=row.get("n_source_clips") or "not measured",
                status="UNDERPOWERED" if underpowered else "measured",
                wer=_show(row.get("wer", ""), percent=True),
                delta=_comparison(row, "delta_wer", percent=True),
                ci=ci,
                win_loss=win_loss,
                calls=_calls_per_clip(row),
                p50=_show(row.get("latency_p50_ms", "")),
                p95=_show(row.get("latency_p95_ms", "")),
            )
        )
    lines.extend([
        "",
        "WER deltas are candidate minus C0; negative favors the candidate. "
        "UNDERPOWERED strata show no comparative claim.",
        "",
    ])
    source_path = results_dir / "by_source_type.csv"
    if source_path.is_file():
        source_rows = _read_csv(source_path)
        lines.extend([
            "## Results by source type",
            "",
            "| Candidate | Model | Source type | Clips | Independent source clips | Status | WER | Delta vs C0 | 95% CI for delta |",
            "|---|---|---|---:|---:|---|---:|---:|---|",
        ])
        for row in source_rows:
            underpowered = _truth(row.get("underpowered", ""))
            ci = (
                "UNDERPOWERED" if underpowered
                else f"{_show(row.get('wer_ci95_low', ''), percent=True)} to "
                f"{_show(row.get('wer_ci95_high', ''), percent=True)}"
            )
            lines.append(
                "| {candidate} | {model} | {source_type} | {clips} | {sources} | "
                "{status} | {wer} | {delta} | {ci} |".format(
                    candidate=row.get("candidate") or "not measured",
                    model=row.get("model") or "not measured",
                    source_type=row.get("group") or row.get("source_type") or "not measured",
                    clips=row.get("clips") or "not measured",
                    sources=row.get("n_source_clips") or "not measured",
                    status="UNDERPOWERED" if underpowered else "measured",
                    wer=_show(row.get("wer", ""), percent=True),
                    delta=_comparison(row, "delta_wer", percent=True),
                    ci=ci,
                )
            )
        lines.append("")
    else:
        lines.extend(["## Results by source type", "", "not measured", ""])
    lines.extend([
        "Real-telephone corpus results and synthetic speech results are always "
        "reported separately. No result is evidence of real-call performance "
        "unless its source is the real-telephone corpus.",
        "",
    ])
    return "\n".join(lines)


def update_readme(readme_path: Path, generated_section: str) -> None:
    text = readme_path.read_text(encoding="utf-8")
    if text.count(README_START) != 1 or text.count(README_END) != 1:
        raise ValueError("README must contain exactly one AUTO-RESULTS marker pair.")
    before, remainder = text.split(README_START, 1)
    _, after = remainder.split(README_END, 1)
    if generated_section.startswith("# Results\n"):
        generated_section = generated_section[len("# Results\n"):].lstrip("\n")
    generated_section = generated_section.replace(
        "](./RUNBOOK.md)", "](docs/RUNBOOK.md)"
    ).replace("](./RESULTS.md)", "](docs/RESULTS.md)")
    readme_path.write_text(
        before + README_START + "\n" + generated_section.strip() + "\n"
        + README_END + after,
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--output", type=Path, default=ROOT.parent / "docs" / "RESULTS.md")
    parser.add_argument("--readme", type=Path, default=ROOT.parent / "README.md")
    args = parser.parse_args()
    try:
        rendered = render_results(args.results_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        update_readme(args.readme, rendered)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"RESULTS REPORT ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Generated {args.output} and updated README result section.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
