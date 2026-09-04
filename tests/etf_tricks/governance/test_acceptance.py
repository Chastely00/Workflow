from etf_tricks.governance.acceptance import build_not_ready_acceptance


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
