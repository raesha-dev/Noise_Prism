import csv
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .scripts.fit_router import MIN_DEV_CLIPS, _validate_min_dev_clips

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _run(script: Path, args: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=90,
    )


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_router_minimum_is_explicit_and_enforced():
    assert MIN_DEV_CLIPS == 10
    with pytest.raises(ValueError, match="At least 10 eligible development clips"):
        _validate_min_dev_clips(MIN_DEV_CLIPS - 1)
    _validate_min_dev_clips(MIN_DEV_CLIPS)


def test_synthetic_dataset_pipeline_never_aggregates_mock_as_benchmark(tmp_path):
    samples = tmp_path / "samples"
    data = tmp_path / "data"
    generated = _run(
        PACKAGE / "scripts" / "make_demo_samples.py",
        ["--samples-dir", str(samples), "--data-dir", str(data), "--tone-clips", "3", "--seed", "31"],
        ROOT,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    index = json.loads((samples / "index.json").read_text(encoding="utf-8"))
    assert index
    assert all(entry["description"].startswith("synthetic") for entry in index)
    assert all("reference" not in entry for entry in index if entry["group"] == "synthetic_tone")
    assert {path.name for path in (data / "noise").iterdir() if path.is_dir()} == {
        "white", "babble_like", "mains_hum", "horn_like",
    }
    assert (data / "noise" / "LICENSES.csv").is_file()

    clean = tmp_path / "clean"
    prepared = _run(
        PACKAGE / "scripts" / "prepare_real_set.py",
        ["--input-csv", str(samples / "demo_manifest.csv"), "--output-dir", str(clean),
         "--test-fraction", "0.25", "--seed", "17"],
        ROOT,
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    clean_dev, clean_test = _rows(clean / "dev.csv"), _rows(clean / "test.csv")
    assert {row["speaker_id"] for row in clean_dev}.isdisjoint(
        {row["speaker_id"] for row in clean_test}
    )

    mixed = tmp_path / "mixed"
    built = _run(
        PACKAGE / "scripts" / "build_eval_set.py",
        ["--manifest", str(clean / "dev.csv"), "--noise-dir", str(data / "noise"),
         "--output-dir", str(mixed), "--snrs", "0", "5", "10",
         "--test-fraction", "0.25", "--seed", "23", "--legacy-plumbing-only"],
        ROOT,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    build_log = json.loads((mixed / "build_log.json").read_text(encoding="utf-8"))
    assert build_log["sources"]
    assert all(
        abs(item["measured_linear_mix_snr_db"] - item["target_snr_db"]) < 0.5
        for item in build_log["sources"]
    )
    mixed_dev, mixed_test = _rows(mixed / "dev.csv"), _rows(mixed / "test.csv")
    assert {row["speaker_id"] for row in mixed_dev}.isdisjoint(
        {row["speaker_id"] for row in mixed_test}
    )
    snr_dev_ids = {row["id"] for row in _rows(mixed / "snr_dev.csv")}
    snr_test_ids = {row["id"] for row in _rows(mixed / "snr_test.csv")}
    assert snr_dev_ids.isdisjoint(snr_test_ids)
    assert snr_test_ids == {row["id"] for row in mixed_test}

    candidates_dir = tmp_path / "candidates"
    candidates = _run(
        PACKAGE / "scripts" / "make_candidate_manifests.py",
        ["--manifest", str(mixed / "dev.csv"), "--output-dir", str(candidates_dir),
         "--candidates", "C0", "C1", "C2", "--seed", "29"],
        ROOT,
    )
    assert candidates.returncode == 0, candidates.stdout + candidates.stderr
    candidate_ids = [_rows(candidates_dir / f"{candidate}.csv") for candidate in ("C0", "C1", "C2")]
    expected_ids = sorted(row["id"] for row in candidate_ids[0])
    assert all(sorted(row["id"] for row in rows) == expected_ids for rows in candidate_ids)

    sweep_source = tmp_path / "one-clip.csv"
    sweep_row = dict(mixed_dev[0])
    sweep_row["audio_path"] = str((mixed / sweep_row["audio_path"]).resolve())
    with sweep_source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(sweep_row))
        writer.writeheader()
        writer.writerow(sweep_row)
    research_candidates = tmp_path / "research-candidates"
    sweep = _run(
        PACKAGE / "scripts" / "make_candidate_manifests.py",
        [
            "--manifest", str(sweep_source),
            "--output-dir", str(research_candidates),
            "--candidates", "C2B", "C2C",
            "--seed", "31",
        ],
        ROOT,
    )
    assert sweep.returncode == 0, sweep.stdout + sweep.stderr
    c2b = _rows(research_candidates / "C2B.csv")
    assert json.loads(c2b[0]["processing_parameters"])["parameters_fitted"] is False
    for coefficient in (0, 0.05, 0.1, 0.2, 0.3, 0.4):
        manifest_path = research_candidates / f"C2C_lambda{coefficient:g}.csv"
        row = _rows(manifest_path)[0]
        assert row["candidate"] == f"C2C_lambda{coefficient:g}"
        assert float(row["oa_lambda"]) == coefficient
        metadata = json.loads(row["processing_parameters"])
        assert metadata["parameters_fitted"] is False
        assert metadata["observation_addition_lambda"] == coefficient

    port = _free_port()
    environment = os.environ.copy()
    environment.update({
        "GNANI_API_KEY": "mock-key",
        "GNANI_BASE_URL": f"http://127.0.0.1:{port}",
        "GNANI_WS_URL": f"ws://127.0.0.1:{port}/stt/v3/stream",
        "BN_DATA_DIR": str(tmp_path),
    })
    mock = subprocess.Popen(
        [sys.executable, "-m", "bharatnoise_api.mock_gnani", "--port", str(port)],
        cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                if mock.poll() is not None:
                    raise AssertionError("Mock Gnani server exited before becoming ready.")
                time.sleep(0.05)
        else:
            raise AssertionError("Mock Gnani server did not become ready.")

        result_paths = []
        for candidate in ("C0", "C1", "C2"):
            result = _run(
                PACKAGE / "run_baseline.py",
                ["--input", str(candidates_dir / f"{candidate}.csv"),
                 "--output", str(tmp_path / "results" / f"{candidate}.csv"),
                 "--candidate", candidate, "--model", "v2.5"],
                ROOT, environment,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            output = tmp_path / "results_mock" / f"MOCK_{candidate}.csv"
            assert output.is_file()
            result_paths.append(output)
            assert all(row["hypothesis"].startswith("[MOCK] ") for row in _rows(output))
        assert not (tmp_path / "results").exists()

        fit = _run(
            PACKAGE / "scripts" / "fit_router.py",
            [
                "--candidate", f"C0={result_paths[0]}",
                "--candidate", f"C1={result_paths[1]}",
                "--snr-csv", str(mixed / "snr_dev.csv"),
                "--repeat-a", str(result_paths[0]), "--repeat-b", str(result_paths[0]),
                "--output", str(tmp_path / "router.json"), "--seed", "37",
            ],
            ROOT,
        )
        assert fit.returncode != 0
        assert "Mock transcripts" in fit.stderr
        assert not (tmp_path / "router.json").exists()
    finally:
        mock.terminate()
        mock.wait(timeout=5)

    mock_aggregate = _run(
        PACKAGE / "scripts" / "aggregate_results.py",
        ["--results-dir", str(tmp_path / "results_mock"), "--n-boot", "200"],
        ROOT,
    )
    assert mock_aggregate.returncode != 0
    assert "Mock transcripts" in mock_aggregate.stderr
    assert not (tmp_path / "results_mock" / "final_results.csv").exists()

    fixture_dir = tmp_path / "aggregate-fixtures"
    fixture_dir.mkdir()
    fields = ["id", "group", "language", "model", "candidate", "run",
              "reference", "hypothesis", "error", "latency_ms"]
    for candidate in ("C0", "C1"):
        with (fixture_dir / f"{candidate}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "id": "schema-only-fixture", "candidate": candidate,
                "error": "No ASR observation exists in this schema-only fixture.",
            })
    aggregated = _run(
        PACKAGE / "scripts" / "aggregate_results.py",
        ["--results-dir", str(fixture_dir), "--n-boot", "200", "--seed", "41"],
        ROOT,
    )
    assert aggregated.returncode == 0, aggregated.stdout + aggregated.stderr
    expected_tables = (
        "final_results.csv", "by_noise.csv", "by_snr.csv", "by_language.csv",
        "ablation_results.csv", "latency.csv",
    )
    for filename in expected_tables:
        assert (fixture_dir / filename).is_file()
    assert _rows(fixture_dir / "final_results.csv") == []
