import pandas as pd
import pytest

from etf_tricks.tier1.stateful_policy import build_stateful_transitions


def _oof(p1: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": [f"momentum-{i}" for i in range(len(p1))],
            "etf_id": "momentum",
            "t0_bar_id": range(len(p1)),
            "p1": p1,
            "prediction_kind": "OOF_CALIBRATED",
            "decision_available_at": pd.date_range("2024-01-02 13:30", periods=len(p1), freq="D"),
        }
    )


def test_policy_uses_all_oof_evidence_but_opens_and_closes_once() -> None:
    result = build_stateful_transitions(_oof([0.6, 0.7, 0.4]), entry_score=0.2, exit_score=-0.1)

    assert pd.isna(result.loc[0, "transition"])
    assert result["transition"].iloc[1:].tolist() == ["flat_to_long", "long_to_flat"]
    assert result["state_after"].tolist() == ["flat", "long", "flat"]
    assert result.loc[1, "evidence_score_after"] == pytest.approx(0.0)
    assert result.loc[2, "evidence_score_after"] == pytest.approx(0.0)


def test_policy_rejects_labels_or_future_outcomes() -> None:
    oof = _oof([0.6])
    oof["y_direction"] = 1

    with pytest.raises(ValueError, match="leakage"):
        build_stateful_transitions(oof, entry_score=0.1, exit_score=-0.1)


def test_policy_is_etf_local_and_requires_strict_bar_order() -> None:
    oof = _oof([0.6, 0.7])
    oof.loc[1, "etf_id"] = "shipping"
    with pytest.raises(ValueError, match="one ETF"):
        build_stateful_transitions(oof, entry_score=0.1, exit_score=-0.1)

    out_of_order = _oof([0.6, 0.7]).iloc[[1, 0]].reset_index(drop=True)
    with pytest.raises(ValueError, match="strictly increasing"):
        build_stateful_transitions(out_of_order, entry_score=0.1, exit_score=-0.1)


def test_policy_uses_one_sided_cusum_so_stale_contrary_evidence_cannot_trap_state() -> None:
    result = build_stateful_transitions(_oof([0.3, 0.7, 0.7, 0.6, 0.4]), entry_score=0.2, exit_score=-0.1)

    assert result["transition"].dropna().tolist() == ["flat_to_long", "long_to_flat"]
    assert result.loc[0, "evidence_score_after"] == 0.0
