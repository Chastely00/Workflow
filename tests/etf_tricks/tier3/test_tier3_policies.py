import pandas as pd

from etf_tricks.tier3.policies import allocate_policy_weights


def _history() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=6)
    return pd.DataFrame(
        {
            "date": list(dates) * 2,
            "etf_id": ["a"] * 6 + ["b"] * 6,
            "daily_return": [0.01, -0.01, 0.01, -0.01, 0.01, -0.01] + [0.02, -0.02, 0.02, -0.02, 0.02, -0.02],
            "available_at": list(dates + pd.Timedelta(hours=14)) * 2,
        }
    )


def test_inverse_vol_uses_only_history_available_before_decision() -> None:
    history = _history()
    base = allocate_policy_weights(["a", "b"], history, "2024-01-07", "inverse_vol", min_observations=4)
    future = pd.concat([history, pd.DataFrame({"date": [pd.Timestamp("2024-02-01")], "etf_id": ["a"], "daily_return": [0.99], "available_at": [pd.Timestamp("2024-02-01 14:00")]})], ignore_index=True)
    appended = allocate_policy_weights(["a", "b"], future, "2024-01-07", "inverse_vol", min_observations=4)

    assert base.status == "READY"
    assert base.weights.to_dict() == appended.weights.to_dict()
    assert base.weights["a"] > base.weights["b"]


def test_hrp_and_other_policies_refuse_a_single_etf_stream() -> None:
    result = allocate_policy_weights(["a"], _history(), "2024-01-07", "hrp", min_observations=4)

    assert result.status == "INSUFFICIENT_CROSS_ETF_UNIVERSE"
    assert result.weights.empty


def test_hrp_returns_normalized_weights_from_past_common_history() -> None:
    result = allocate_policy_weights(["a", "b"], _history(), "2024-01-07", "hrp", min_observations=4)

    assert result.status == "READY"
    assert result.weights.index.tolist() == ["a", "b"]
    assert result.weights.sum() == 1.0
    assert result.weights.gt(0).all()
