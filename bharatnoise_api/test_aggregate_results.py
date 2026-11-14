from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent.parent


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    fields = [
        "id", "split", "group", "noise_type", "target_snr_db", "language",
        "speaker_id", "model", "candidate", "reference", "hypothesis", "error",
        "empty_transcript", "truncated", "latency_ms",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _row(
    clip_id: str,
    candidate: str,
    speaker: str,
    group: str,
    noise: str,
    snr: str,
    hypothesis: str,
    **overrides: str,
) -> dict[str, str]:
    return {
        "id": clip_id,
        "split": "development",
        "group": group,
        "noise_type": noise,
        "target_snr_db": snr,
        "language": "hi-IN",
        "speaker_id": speaker,
        "model": "prisma-v2.5",
        "candidate": candidate,
        "reference": "a b",
        "hypothesis": hypothesis,
        "error": "",
        "empty_transcript": str(not hypothesis.strip()).lower(),
        "truncated": "false",
        "latency_ms": "100",
        **overrides,
    }


def _run(results_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PACKAGE / "scripts" / "aggregate_results.py"),
            "--results-dir",
            str(results_dir),
            "--n-boot",
            "200",
            "--seed",
            "13",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_aggregation_reports_groups_and_empty_failed_truncated_diagnostics(tmp_path):
    baseline = [
        _row("a-1", "C0", "speaker-a", "babble_like_snr0", "babble_like", "0", "a b"),
        _row("b-1", "C0", "speaker-b", "street_snr5", "street", "5", "a b"),
    ]
    experimental = [
        _row("a-1", "C2B", "speaker-a", "babble_like_snr0", "babble_like", "0", ""),
        _row(
            "b-1",
            "C2B",
            "speaker-b",
            "street_snr5",
            "street",
            "5",
            "",
            error="HTTP 400 BAD_REQUEST",
            empty_transcript="false",
            truncated="true",
        ),
    ]
    _write_rows(tmp_path / "C0.csv", baseline)
    _write_rows(tmp_path / "C2B.csv", experimental)

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    final = _read(tmp_path / "final_results.csv")
    candidate = next(row for row in final if row["candidate"] == "C2B")
    assert candidate["split"] == "development"
    assert candidate["transcript_rows"] == "2"
    assert candidate["failed_requests"] == "1"
    assert candidate["empty_transcripts"] == "1"
    assert candidate["truncated_responses"] == "1"
    assert candidate["n_source_clips"] == "1"
    assert candidate["underpowered"] == "True"
    assert candidate["delta_wer"] == ""
    assert candidate["B_better"] == ""
    assert candidate["label_empty"] == "1"
    assert candidate["label_error"] == "1"
    assert candidate["diagnosis_thresholds_fitted"] == "False"
    assert {row["group"] for row in _read(tmp_path / "by_noise.csv")} == {"babble_like", "street"}
    assert {row["snr_db"] for row in _read(tmp_path / "by_snr.csv")} == {"0.0", "5.0"}


def test_aggregation_refuses_to_mix_development_and_heldout_rows(tmp_path):
    _write_rows(
        tmp_path / "C0.csv",
        [_row("a-1", "C0", "speaker-a", "noise_snr0", "noise", "0", "a b")],
    )
    heldout = _row("a-1", "C1", "speaker-a", "noise_snr0", "noise", "0", "a b")
    heldout["split"] = "held-out"
    _write_rows(tmp_path / "C1.csv", [heldout])

    result = _run(tmp_path)

    assert result.returncode != 0
    assert "must be aggregated separately" in result.stderr
    assert not (tmp_path / "final_results.csv").exists()
