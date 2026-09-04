"""Validate Tier 2 sources before any Tier 3 allocation research."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Tier3ResearchSource:
    etf_id: str
    accepted_handoff: pd.DataFrame
    sealed_status: str


def validate_tier2_research_source(
    handoff: pd.DataFrame, manifest: dict[str, object]
) -> Tier3ResearchSource:
    """Accept a single ETF's research-only Tier 2 OOF artifact, never PnL data."""
    if manifest.get("schema_version") != "tier2-oof-v1":
        raise ValueError("Tier 3 requires a tier2-oof-v1 manifest")
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("research_only") is not True:
        raise ValueError("Tier 3 research source must be explicitly research-only")
    if metadata.get("sealed_status") != "NOT_SEALED":
        raise ValueError("Tier 3 research contract accepts only explicit NOT_SEALED sources")
    required = {
        "event_id", "etf_id", "t0_bar_id", "p2", "accepted",
        "acceptance_threshold", "acceptance_reason", "prediction_kind",
        "tier2_decision_available_at",
    }
    if missing := required.difference(handoff.columns):
        raise ValueError(f"Tier 3 source missing columns: {sorted(missing)}")
    forbidden = {
        "t0", "t1", "y_meta", "y_direction", "net_log_return", "net_simple_return",
        "gross_simple_return", "exit_date", "entry_date", "order", "shares", "allocation",
    }
    if present := forbidden.intersection(handoff.columns):
        raise ValueError(f"Tier 3 source has forbidden future/PnL columns: {sorted(present)}")
    if handoff.empty or handoff["event_id"].duplicated().any():
        raise ValueError("Tier 3 source requires nonempty unique event_id")
    if handoff["etf_id"].astype(str).nunique() != 1:
        raise ValueError("Tier 3 source must contain exactly one ETF")
    etf_id = str(handoff["etf_id"].iloc[0])
    if metadata.get("etf_scope") != etf_id:
        raise ValueError("Tier 3 source manifest ETF scope mismatch")
    if not handoff["prediction_kind"].eq("OOF_CALIBRATED").all():
        raise ValueError("Tier 3 source requires calibrated Tier 2 OOF predictions")
    accepted = handoff.loc[handoff["accepted"].astype(bool)].copy()
    return Tier3ResearchSource(etf_id, accepted, "NOT_SEALED")
