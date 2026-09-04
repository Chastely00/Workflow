"""Fail-closed acceptance state for incomplete AFML strategy lineages."""

from __future__ import annotations

from typing import Any

import pandas as pd


def build_final_acceptance(
    *,
    trial_count: float,
    sealed_status: str,
    tier2_status: str,
    tier3_status: str,
    paper_ledger_ready: bool,
    allocation_policies: tuple[str, ...],
    dsr: float | None,
) -> dict[str, Any]:
    """Return the only admissible final status from explicit evidence gates."""
    if trial_count <= 0:
        raise ValueError("trial_count must be positive")
    required_policies = {"equal_capital", "inverse_vol", "hrp"}
    missing: list[str] = []
    if sealed_status != "SEALED":
        missing.append("sealed_test")
    if tier2_status != "SEALED_PASSED":
        missing.append("tier2_sealed_admission")
    if tier3_status != "SEALED_PASSED":
        missing.append("tier3_sealed_admission")
    if not paper_ledger_ready:
        missing.append("reconciled_paper_ledger")
    if not required_policies.issubset(allocation_policies):
        missing.append("three_policy_ledger_comparison")
    if dsr is None:
        missing.append("dsr")
        dsr_status = "NOT_COMPUTABLE_NO_PAPER_LEDGER" if not paper_ledger_ready else "NOT_COMPUTABLE"
    elif not 0 <= dsr <= 1:
        raise ValueError("dsr must be within [0, 1]")
    elif dsr < 0.95:
        missing.append("dsr_at_least_0_95")
        dsr_status = "BELOW_THRESHOLD"
    else:
        dsr_status = "PASSED"
    return {
        "status": "PAPER_TRADE_ELIGIBLE" if not missing else "NOT_READY",
        "effective_independent_trial_count": float(trial_count),
        "sealed_status": sealed_status,
        "tier2_status": tier2_status,
        "tier3_status": tier3_status,
        "paper_ledger_ready": bool(paper_ledger_ready),
        "allocation_policies": sorted(set(allocation_policies)),
        "dsr": dsr,
        "dsr_status": dsr_status,
        "missing_requirements": missing,
    }


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


def build_current_lineage_acceptance(
    *,
    trial_count: float,
    tier1_gate_table: pd.DataFrame,
    tier2_status_by_etf: dict[str, str],
) -> dict[str, Any]:
    """Summarize a current lineage without misusing a legacy sealed report."""
    required = {"etf_id", "status", "tier2_permitted", "tier3_permitted"}
    if missing := required.difference(tier1_gate_table.columns):
        raise ValueError(f"Tier 1 gate table missing columns: {sorted(missing)}")
    if tier1_gate_table.empty or tier1_gate_table["etf_id"].duplicated().any():
        raise ValueError("Tier 1 gate table requires unique nonempty ETF rows")
    passers = sorted(
        tier1_gate_table.loc[tier1_gate_table["status"].eq("PASSED"), "etf_id"].astype(str)
    )
    if set(tier2_status_by_etf) != set(passers):
        raise ValueError("Tier 2 statuses must match exactly the Tier 1 passing ETF set")
    statuses = set(tier2_status_by_etf.values())
    if statuses == {"SEALED_PASSED"}:
        tier2_status = "SEALED_PASSED"
    elif "RESEARCH_ONLY" in statuses:
        tier2_status = "RESEARCH_ONLY"
    elif statuses == {"INSUFFICIENT_MATURE_EVENTS"}:
        tier2_status = "INSUFFICIENT_MATURE_EVENTS"
    else:
        tier2_status = "MIXED_NOT_ADMITTED"
    report = build_final_acceptance(
        trial_count=trial_count,
        sealed_status="NOT_SEALED",
        tier2_status=tier2_status,
        tier3_status="NOT_STARTED",
        paper_ledger_ready=False,
        allocation_policies=(),
        dsr=None,
    )
    return {
        **report,
        "tier1_passed_etfs": passers,
        "tier2_status_by_etf": dict(sorted(tier2_status_by_etf.items())),
        "tier1_gate_status_counts": {
            str(key): int(value)
            for key, value in tier1_gate_table["status"].value_counts().items()
        },
    }
