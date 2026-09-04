import pandas as pd
import pytest

from etf_tricks.tier1.extended_oof import prepend_earlier_oof


def _frame(ids: list[int]) -> pd.DataFrame:
    return pd.DataFrame({"event_id": [f"x-{i}" for i in ids], "etf_id": "x", "t0_bar_id": ids, "p1": [0.6] * len(ids), "prediction_kind": "OOF_CALIBRATED", "decision_available_at": pd.date_range("2020-01-01", periods=len(ids), freq="D")})


def test_extension_can_only_prepend_earlier_oof_rows_without_rewriting_existing_rows() -> None:
    existing = _frame([10, 11])
    extension = _frame([8, 9, 10])

    result = prepend_earlier_oof(existing, extension)

    assert result["t0_bar_id"].tolist() == [8, 9, 10, 11]
    assert result.iloc[-2:].reset_index(drop=True).equals(existing.reset_index(drop=True))


def test_extension_rejects_non_local_or_duplicate_earlier_rows() -> None:
    with pytest.raises(ValueError, match="one ETF"):
        prepend_earlier_oof(_frame([10]), _frame([8]).assign(etf_id="y"))
    with pytest.raises(ValueError, match="duplicate"):
        prepend_earlier_oof(_frame([10]), _frame([8, 8]))
