from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_target_artifact(targets: pd.DataFrame, output_dir: str | Path, metadata: dict[str, object]) -> dict[str, object]:
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"Tier 1 output already exists: {output}")
    if targets.empty or "event_id" not in targets or targets["event_id"].duplicated().any():
        raise ValueError("targets require nonempty unique event_id")
    output.mkdir(parents=True)
    table = output / "targets.parquet"
    targets.to_parquet(table, index=False)
    manifest = {"schema_version": "tier1-target-v1", "metadata": metadata, "tables": {"targets": {"path": table.name, "row_count": len(targets), "sha256": _sha256(table)}}}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return manifest
