"""Fail-closed acceptance state for incomplete AFML strategy lineages."""

from __future__ import annotations

from typing import Any


def build_not_ready_acceptance(
    *,
    trial_count: float,
    tier1_gate: dict[str, Any],
    sealed_summary: dict[str, Any],
) -> dict[str, Any]:
    """Describe why a failed Tier 1 lineage cannot progress downstream."""
    if trial_count <= 0:
        raise ValueError("trial_count must be positive")
    if tier1_gate.get("status") != "FAILED":
        raise ValueError("NOT_READY report requires a failed Tier 1 gate")
    if tier1_gate.get("tier2_permitted") or tier1_gate.get("tier3_permitted"):
        raise ValueError("failed Tier 1 gate must prohibit downstream layers")
    if not sealed_summary.get("selected_etf_id"):
        raise ValueError("sealed summary requires selected_etf_id")
    return {
        "status": "NOT_READY",
        "tier1_gate_status": "FAILED",
        "tier2_permitted": False,
        "tier3_permitted": False,
        "effective_independent_trial_count": float(trial_count),
        "dsr_status": "NOT_COMPUTABLE_NO_PAPER_LEDGER",
        "sealed_summary": sealed_summary,
        "failure_layers": ["tier1_discrimination", "tier1_candidate_economics", "sealed_confirmation"],
        "next_requirements": [
            "materially_new_pit_safe_hypothesis",
            "new_unseen_evaluation_interval",
            "fresh_pre_registered_trial",
            "tier1_gate_before_tier2_or_tier3",
        ],
    }
