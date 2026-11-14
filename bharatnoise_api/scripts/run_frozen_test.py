"""Run the held-out benchmark once, after verifying the frozen code/config hash."""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from ..env import load_environment
    from .freeze_config import ROOT, verify_frozen_config
    from .plan_budget import VALID_CANDIDATES, VALID_MODELS, plan_manifest, project_path
    from ..gnani_client import endpoint_mode
except ImportError:
    from env import load_environment
    from freeze_config import ROOT, verify_frozen_config
    from plan_budget import VALID_CANDIDATES, VALID_MODELS, plan_manifest, project_path
    from gnani_client import endpoint_mode

STATE_PATH = ROOT / "data" / "held_out_run_state.json"


def _validate_test_manifest(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(row.get("split", "").strip().lower() not in {"test", "held-out", "heldout"} for row in rows):
        raise ValueError("The manifest must contain only explicitly held-out/test rows.")
    if len({row.get("id", "") for row in rows}) != len(rows):
        raise ValueError(f"Candidate test manifest has duplicate clip IDs: {path.name}.")
    return {row.get("id", "") for row in rows}


def _candidate_manifest(value: str) -> tuple[str, str, Path]:
    try:
        candidate, filename = value.split("=", 1)
    except ValueError:
        raise ValueError("--candidate-manifest values must be CANDIDATE=CSV.") from None
    fallback = candidate.split("+", 1)[0]
    if fallback not in VALID_CANDIDATES:
        raise ValueError(f"Unsupported candidate label: {candidate}.")
    return candidate, fallback, project_path(Path(filename)).resolve()


def _write_state(path: Path, payload: dict[str, object], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", action="append", required=True, metavar="CANDIDATE=CSV")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--models", nargs="+", choices=VALID_MODELS, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--confirm-calls", type=int, required=True,
                        help="Must exactly equal the plan_budget call count.")
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    args = parser.parse_args()
    try:
        frozen = verify_frozen_config()
        candidate_manifests = [_candidate_manifest(value) for value in args.candidate_manifest]
        labels = [candidate for candidate, _, _ in candidate_manifests]
        if len(set(labels)) != len(labels):
            raise ValueError("Each candidate must have exactly one test manifest.")
        expected_ids: set[str] | None = None
        calls = 0
        audio_minutes = 0.0
        for _, fallback, manifest in candidate_manifests:
            ids = _validate_test_manifest(manifest)
            if expected_ids is None:
                expected_ids = ids
            elif ids != expected_ids:
                raise ValueError("All candidate test manifests must contain the same clip IDs.")
            candidate_calls, candidate_minutes = plan_manifest(manifest, [fallback], args.models, 1)
            calls += candidate_calls
            audio_minutes += candidate_minutes
    except ValueError as exc:
        print(f"HELD-OUT RUN BLOCKED: {exc}", file=sys.stderr)
        return 2
    if calls != args.confirm_calls:
        print(f"HELD-OUT RUN BLOCKED: expected --confirm-calls {calls}, received {args.confirm_calls}.",
              file=sys.stderr)
        return 2
    load_environment()
    if endpoint_mode() != "live":
        print("HELD-OUT RUN BLOCKED: configure the official Gnani endpoint and API key before consuming the one-time run.",
              file=sys.stderr)
        return 2
    state_path = project_path(args.state).resolve()
    state = {
        "status": "started",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "calls_planned": calls,
        "audio_minutes_planned": round(audio_minutes, 6),
        "frozen_sha256": frozen["code_config_sha256"],
    }
    try:
        _write_state(state_path, state, exclusive=True)
    except FileExistsError:
        print("HELD-OUT RUN BLOCKED: the one-time run state already exists.", file=sys.stderr)
        return 2
    try:
        completed_code = 0
        environment = os.environ.copy()
        environment["BN_FROZEN_TEST_RUN_STATE"] = str(state_path)
        for candidate, fallback, manifest in candidate_manifests:
            command = [
                sys.executable, str(ROOT / "run_baseline.py"),
                "--input", str(manifest),
                "--output", str(project_path(args.results_dir).resolve() / f"held_out_{candidate}.csv"),
                "--models", *args.models,
                "--candidate", fallback,
                "--format", "verbatim",
                "--split", "held-out",
                "--runs", "1",
                "--run-id", f"{args.run_id}-{candidate}",
            ]
            completed = subprocess.run(command, cwd=ROOT, env=environment, check=False)
            if completed.returncode != 0:
                completed_code = completed.returncode
                break
    except OSError as exc:
        state.update({"status": "failed", "error_type": type(exc).__name__})
        _write_state(state_path, state)
        print(f"HELD-OUT RUN FAILED: {type(exc).__name__}", file=sys.stderr)
        return 3
    state.update({
        "status": "completed" if completed_code == 0 else "failed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": completed_code,
    })
    _write_state(state_path, state)
    print(f"Held-out run finished with exit code {completed_code}; planned calls={calls}, audio={audio_minutes:.3f} min.")
    return completed_code


if __name__ == "__main__":
    raise SystemExit(main())
