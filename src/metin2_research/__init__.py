"""Safe Metin2 research-sandbox perception and policy harness."""

__version__ = "0.2.0"

import os
from pathlib import Path
from typing import Any


def find_project_root() -> Path:
    """Find the metin2_research project root by walking up from this file."""
    return Path(__file__).resolve().parent.parent.parent  # src/metin2_research/ -> ../../


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the YAML config file. Falls back to config.yaml in project root."""
    import yaml

    if path is None:
        path = find_project_root() / "config.yaml"
    path = Path(path)
    if not path.exists():
        return {}  # empty config, everything uses defaults
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}