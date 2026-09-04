import pandas as pd
import pytest

from etf_tricks.tier3.input_contract import validate_tier2_research_source


def _handoff() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["x"], "etf_id": ["low_volatility"], "t0_bar_id": [1],
            "p2": [0.7], "accepted": [True], "acceptance_threshold": [0.5],
            "acceptance_reason": ["p2_at_or_above_fold_threshold"],
            "prediction_kind": ["OOF_CALIBRATED"],
            "tier2_decision_available_at": pd.to_datetime(["2024-01-02 14:00+08:00"]),
        }
    )


def test_tier3_source_contract_accepts_only_single_etf_not_sealed_research_handoff() -> None:
    source = validate_tier2_research_source(
        _handoff(),
        {"schema_version": "tier2-oof-v1", "metadata": {"research_only": True, "sealed_status": "NOT_SEALED", "etf_scope": "low_volatility"}},
    )

    assert source.etf_id == "low_volatility"
    assert source.accepted_handoff["accepted"].tolist() == [True]


def test_tier3_source_contract_rejects_future_target_columns() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        validate_tier2_research_source(
            _handoff().assign(net_log_return=0.01),
            {"schema_version": "tier2-oof-v1", "metadata": {"research_only": True, "sealed_status": "NOT_SEALED", "etf_scope": "low_volatility"}},
        )
