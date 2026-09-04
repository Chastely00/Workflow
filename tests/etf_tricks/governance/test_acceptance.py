from etf_tricks.governance.acceptance import build_not_ready_acceptance
from etf_tricks.governance.acceptance import build_final_acceptance


def test_acceptance_reports_not_ready_without_promotable_tier1_or_paper_ledger() -> None:
    report = build_not_ready_acceptance(
        trial_count=30.0,
        tier1_gate={"status": "FAILED", "tier2_permitted": False, "tier3_permitted": False},
        sealed_summary={"selected_etf_id": "momentum", "sealed_auc": 0.13},
    )

    assert report["status"] == "NOT_READY"
    assert report["tier2_permitted"] is False
    assert report["tier3_permitted"] is False
    assert report["dsr_status"] == "NOT_COMPUTABLE_NO_PAPER_LEDGER"
    assert report["effective_independent_trial_count"] == 30.0


def test_final_acceptance_is_not_ready_when_sealed_ledger_and_dsr_are_missing() -> None:
    report = build_final_acceptance(
        trial_count=138.0,
        sealed_status="NOT_SEALED",
        tier2_status="RESEARCH_ONLY",
        tier3_status="INSUFFICIENT_CROSS_ETF_UNIVERSE",
        paper_ledger_ready=False,
        allocation_policies=(),
        dsr=None,
    )

    assert report["status"] == "NOT_READY"
    assert report["dsr_status"] == "NOT_COMPUTABLE_NO_PAPER_LEDGER"
    assert "sealed_test" in report["missing_requirements"]
    assert "three_policy_ledger_comparison" in report["missing_requirements"]
