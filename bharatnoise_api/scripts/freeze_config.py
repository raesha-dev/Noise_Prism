"""Freeze development-selected parameters and verify their code/configuration hash."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_LAMBDAS = (0.0, 0.05, 0.1, 0.2, 0.3, 0.4)
FROZEN_PATH = ROOT / "config" / "frozen.json"


def _hashed_files() -> list[Path]:
    sources = [
        *ROOT.glob("*.py"),
        *(ROOT / "audio").rglob("*.py"),
        *(ROOT / "scripts").rglob("*.py"),
        *(path for path in (ROOT / "config").glob("*.json") if path.name != "frozen.json"),
    ]
    return sorted(path for path in sources if path.is_file() and not path.name.startswith("test_"))


def fingerprint() -> tuple[str, dict[str, str]]:
    files = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in _hashed_files()
    }
    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest, files


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read configuration {path.name}: {type(exc).__name__}.") from None
    if not isinstance(value, dict):
        raise ValueError(f"Configuration {path.name} must contain a JSON object.")
    return value


def freeze(observation_lambda: float | None, output: Path = FROZEN_PATH) -> dict[str, Any]:
    router = _read_json(ROOT / "config" / "router.json")
    suppress = _read_json(ROOT / "config" / "suppress.json")
    rules = router.get("rules")
    baseline_only = (
        router.get("baseline_only") is True
        and isinstance(rules, list)
        and bool(rules)
        and all(isinstance(rule, dict) and rule.get("variant") == "C0" for rule in rules)
    )
    if router.get("fitted") is not True and not baseline_only:
        raise ValueError("router.json must be fitted on development data before freezing.")
    if not isinstance(rules, list) or not rules:
        raise ValueError("router.json must contain fitted routing rules.")
    uses_suppression = any(
        isinstance(rule, dict) and rule.get("variant") in {"C2", "C2B", "C2C"}
        for rule in rules
    )
    uses_non_original_candidate = any(
        isinstance(rule, dict) and rule.get("variant") != "C0" for rule in rules
    )
    if uses_non_original_candidate and router.get("routing_estimator_validated") is not True:
        raise ValueError("A non-C0 router requires a validated routing estimator.")
    if (uses_suppression or observation_lambda is not None) and suppress.get("fitted") is not True:
        raise ValueError("Suppressor parameters must be fitted before a suppression/OA candidate is frozen.")
    if observation_lambda is not None and observation_lambda not in ALLOWED_LAMBDAS:
        raise ValueError(f"observation_lambda must be one of {ALLOWED_LAMBDAS} or omitted.")
    digest, files = fingerprint()
    payload = {
        "fitted": True,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "observation_lambda": observation_lambda,
        "router": router,
        "suppression": suppress,
        "code_config_sha256": digest,
        "file_sha256": files,
        "note": (
            "Frozen baseline-only C0 configuration; no router parameters were fitted."
            if baseline_only and router.get("fitted") is not True
            else "Frozen from development evidence. Held-out results were not used for selection."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def verify_frozen_config(path: Path = FROZEN_PATH) -> dict[str, Any]:
    payload = _read_json(path)
    if payload.get("fitted") is not True:
        raise ValueError("Frozen configuration is not marked fitted.")
    digest, files = fingerprint()
    if payload.get("file_sha256") != files or payload.get("code_config_sha256") != digest:
        raise ValueError("Frozen configuration hash does not match the current code/configuration.")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation-lambda", choices=("none", *(str(value) for value in ALLOWED_LAMBDAS)),
                        required=True)
    parser.add_argument("--output", type=Path, default=FROZEN_PATH)
    args = parser.parse_args()
    value = None if args.observation_lambda == "none" else float(args.observation_lambda)
    try:
        payload = freeze(value, args.output)
    except ValueError as exc:
        print(f"FREEZE BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(f"Frozen configuration written: {args.output}")
    print(f"Code/config SHA-256: {payload['code_config_sha256']}")
    print("No held-out audio or results were read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
