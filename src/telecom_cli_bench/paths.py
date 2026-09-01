"""Project path discovery.

All runtime paths are anchored to the repository instead of the caller's current
working directory. ``TCB_PROJECT_ROOT`` can override discovery for packaged or
custom layouts.
"""

from __future__ import annotations

import os
from pathlib import Path


def _looks_like_project_root(path: Path) -> bool:
    return (path / "data" / "tasks").is_dir() and (path / "configs" / "prompts").is_dir()


def discover_project_root() -> Path:
    override = os.getenv("TCB_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    # Editable/source checkout: <root>/src/telecom_cli_bench/paths.py
    source_root = Path(__file__).resolve().parents[2]
    if _looks_like_project_root(source_root):
        return source_root

    # Fallback for an installed package invoked while somewhere inside the repo.
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if _looks_like_project_root(candidate):
            return candidate

    # Keep the failure deterministic; callers will emit a useful missing-path error.
    return source_root


PROJECT_ROOT = discover_project_root()
TASK_DIR = PROJECT_ROOT / "data" / "tasks"
DEMO_FILE = TASK_DIR / "demo.jsonl"
VOCAB_DIR = PROJECT_ROOT / "data" / "vocab"
PROMPT_DIR = PROJECT_ROOT / "configs" / "prompts"
RAW_DIR = PROJECT_ROOT / "results" / "raw"
SCORE_DIR = PROJECT_ROOT / "results" / "scored"
SCRIPT_DIR = PROJECT_ROOT / "scripts"
IMAGE_DIR = PROJECT_ROOT / "docs" / "images"
