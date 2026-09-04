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


def write_feature_extension_artifact(
    features: pd.DataFrame,
    output_dir: str | Path,
    metadata: dict[str, object],
) -> dict[str, object]:
    """Persist a keyed, read-only feature sidecar without touching AFML v5."""
    output = Path(output_dir)
    required = {"etf_id", "bar_id", "feature_available_at"}
    if output.exists():
        raise FileExistsError(f"Tier 1 output already exists: {output}")
    if missing := required.difference(features.columns):
        raise ValueError(f"feature extension missing columns: {sorted(missing)}")
    if features.empty or features.duplicated(["etf_id", "bar_id"]).any():
        raise ValueError("feature extension requires nonempty unique etf_id-bar_id keys")
    if pd.to_datetime(features["feature_available_at"], errors="coerce").isna().any():
        raise ValueError("feature extension requires valid feature availability")
    output.mkdir(parents=True)
    table = output / "features.parquet"
    features.to_parquet(table, index=False)
    manifest = {
        "schema_version": "tier1-feature-extension-v1",
        "metadata": metadata,
        "tables": {
            "features": {
                "path": table.name,
                "row_count": len(features),
                "sha256": _sha256(table),
            }
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def write_oof_artifact(handoff: pd.DataFrame, output_dir: str | Path, metadata: dict[str, object]) -> dict[str, object]:
    """Persist an immutable Tier 1 OOF-only hand-off artifact."""
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"Tier 1 output already exists: {output}")
    required = {"event_id", "etf_id", "t0_bar_id", "side", "p1", "candidate_indicator", "candidate_threshold", "candidate_reason", "prediction_kind", "decision_available_at"}
    if missing := required.difference(handoff.columns):
        raise ValueError(f"OOF hand-off missing columns: {sorted(missing)}")
    forbidden = {"t1", "y_direction", "target_status", "trigger_date", "entry_date", "entry_raw_open", "exit_date", "exit_raw_open", "net_log_return"}
    if present := forbidden.intersection(handoff.columns):
        raise ValueError(f"OOF hand-off has forbidden future target columns: {sorted(present)}")
    if handoff.empty or handoff["event_id"].duplicated().any():
        raise ValueError("OOF hand-off requires nonempty unique event_id")
    if not handoff["prediction_kind"].eq("OOF_CALIBRATED").all():
        raise ValueError("OOF hand-off requires calibrated OOF predictions")
    output.mkdir(parents=True)
    table = output / "oof_handoff.parquet"
    handoff.to_parquet(table, index=False)
    manifest = {"schema_version": "tier1-oof-v1", "metadata": metadata, "tables": {"oof_handoff": {"path": table.name, "row_count": len(handoff), "sha256": _sha256(table)}}}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return manifest
