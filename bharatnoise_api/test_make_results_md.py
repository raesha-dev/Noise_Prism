import csv

import pytest

from .scripts.make_results_md import render_results, update_readme


def test_missing_results_are_reported_as_not_measured(tmp_path):
    report = render_results(tmp_path)

    assert "No measured benchmark results" in report
    assert "not measured" in report
    assert "UNDERPOWERED" not in report


def test_raw_result_files_are_not_mistaken_for_aggregated_metrics(tmp_path):
    (tmp_path / "C0.csv").write_text("candidate,model\nC0,v2.5\n", encoding="utf-8")

    report = render_results(tmp_path)

    assert "exist but have not been aggregated" in report
    assert "not reported until" in report


def test_aggregate_values_render_and_underpowered_comparisons_are_suppressed(tmp_path):
    with (tmp_path / "final_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "candidate", "model", "split", "clips", "n_source_clips", "underpowered",
            "wer", "delta_wer", "wer_ci95_low", "wer_ci95_high", "B_better", "tie",
            "A_better", "latency_p50_ms", "latency_p95_ms",
        ])
        writer.writeheader()
        writer.writerow({
            "candidate": "C1", "model": "prisma-v3.0", "split": "dev",
            "clips": 12, "n_source_clips": 6, "underpowered": "true", "wer": 0.25,
            "delta_wer": "", "wer_ci95_low": "", "wer_ci95_high": "", "B_better": "",
            "tie": "", "A_better": "", "latency_p50_ms": 100, "latency_p95_ms": 150,
        })

    report = render_results(tmp_path)

    assert "25.00%" in report
    assert "UNDERPOWERED" in report
    assert "100.0" in report


def test_readme_update_requires_one_marker_pair(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("before\n<!-- AUTO-RESULTS:START -->\nold\n<!-- AUTO-RESULTS:END -->\nafter\n")

    update_readme(readme, "new table")

    assert "new table" in readme.read_text()
    assert "old" not in readme.read_text()
    readme.write_text("missing markers")
    with pytest.raises(ValueError, match="marker pair"):
        update_readme(readme, "not written")


def test_readme_report_links_point_to_docs_directory(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "<!-- AUTO-RESULTS:START --><!-- AUTO-RESULTS:END -->",
        encoding="utf-8",
    )

    update_readme(readme, "[runbook](./RUNBOOK.md)")

    assert "[runbook](docs/RUNBOOK.md)" in readme.read_text(encoding="utf-8")
