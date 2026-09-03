from __future__ import annotations

import json
from pathlib import Path

from data_analysts.filesystem import replace_file
from data_analysts.paths import DataAnalystsContext, FORBIDDEN_ARTIFACT_PATH_SEGMENTS


def write_diagnostic(
    context: DataAnalystsContext, name: str, payload: dict[str, object]
) -> Path:
    safe_parts = _diagnostic_name_parts(name)
    path = context.store_path("diagnostics", *safe_parts).with_suffix(".json")
    staging = path.with_name(f".{path.name}.tmp")
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        replace_file(staging, path)
    finally:
        if staging.exists():
            staging.unlink()
    return path


def _diagnostic_name_parts(name: str) -> list[str]:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError("diagnostic name must be relative")

    safe_parts: list[str] = []
    for part in normalized.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise ValueError("diagnostic name cannot contain parent traversal")
        if ":" in part:
            raise ValueError("diagnostic name cannot contain drive-like parts")
        if part in FORBIDDEN_ARTIFACT_PATH_SEGMENTS:
            raise ValueError(f"diagnostic name cannot contain forbidden segment: {part}")
        safe_parts.append(part)

    if not safe_parts:
        raise ValueError("diagnostic name is required")
    return safe_parts
