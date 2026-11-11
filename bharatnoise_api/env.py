"""Load project-local environment files without overriding the real environment."""
from __future__ import annotations

import os
import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_ROOT.parent
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _minimal_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not _ENV_NAME.fullmatch(name):
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[name] = value
    return values


def load_environment() -> None:
    """Load package then repository `.env`; process environment always has priority."""
    paths = (PACKAGE_ROOT / ".env", REPOSITORY_ROOT / ".env")
    try:
        from dotenv import dotenv_values
    except ImportError:
        dotenv_values = None

    combined: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        entries = dotenv_values(path) if dotenv_values is not None else _minimal_values(path)
        combined.update({key: value for key, value in entries.items() if value is not None})
    for key, value in combined.items():
        os.environ.setdefault(key, value)
