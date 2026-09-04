"""Immutable Tier 2 OOF hand-off artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_tier2_oof_artifact(
    handoff: pd.DataFrame, output_dir: str | Path, metadata: dict[str, object]
) -> dict[str, object]:
    """Persist a non-overwriting research-only Tier 2 OOF hand-off."""
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"Tier 2 output already exists: {output}")
    if metadata.get("research_only") is not True or metadata.get("sealed_status") != "NOT_SEALED":
        raise ValueError("Tier 2 OOF artifact must be explicitly research-only and NOT_SEALED")
    required = {
        "event_id", "etf_id", "t0_bar_id", "p2", "accepted",
        "acceptance_threshold", "acceptance_reason", "prediction_kind",
        "tier2_decision_available_at",
    }
    if missing := required.difference(handoff.columns):
        raise ValueError(f"Tier 2 OOF hand-off missing columns: {sorted(missing)}")
    forbidden = {
        "t0", "t1", "y_meta", "y_direction", "target_status", "trigger_date",
        "entry_date", "entry_raw_open", "exit_date", "exit_raw_open", "net_log_return",
        "gross_simple_return", "net_simple_return", "allocation", "order", "shares",
    }
    if present := forbidden.intersection(handoff.columns):
        raise ValueError(f"Tier 2 OOF hand-off has forbidden future/allocation columns: {sorted(present)}")
    if handoff.empty or handoff["event_id"].duplicated().any():
        raise ValueError("Tier 2 OOF hand-off requires nonempty unique event_id")
    if handoff["etf_id"].astype(str).nunique() != 1:
        raise ValueError("Tier 2 OOF hand-off must contain exactly one ETF")
    if not handoff["prediction_kind"].eq("OOF_CALIBRATED").all():
        raise ValueError("Tier 2 OOF hand-off requires calibrated OOF predictions")
    if handoff[["p2", "accepted", "acceptance_reason"]].isna().any().any():
        raise ValueError("Tier 2 OOF hand-off requires complete prediction metadata")
    output.mkdir(parents=True)
    table = output / "oof_handoff.parquet"
    handoff.to_parquet(table, index=False)
    manifest = {
        "schema_version": "tier2-oof-v1",
        "metadata": metadata,
        "tables": {"oof_handoff": {"path": table.name, "row_count": len(handoff), "sha256": _sha256(table)}},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return manifest
