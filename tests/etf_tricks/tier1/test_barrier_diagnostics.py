import pandas as pd
import pytest

from etf_tricks.tier1.barrier_diagnostics import summarize_barriers


def _events() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["e1", "e2"], "etf_id": "momentum", "t0_bar_id": [1, 2],
            "t0_date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "entry_price": [100.0, 100.0], "first_touch_type": ["upper", "lower"],
            "first_touch_bar_id": [3, 3], "target_status": ["resolved", "resolved"],
        }
    )


def _paths() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["e1", "e1", "e1", "e2", "e2"],
            "bar_id": [2, 3, 4, 3, 4],
            "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-03", "2024-01-04"]),
            "close_nav": [101.0, 105.0, 108.0, 96.0, 95.0],
        }
    )


def test_barrier_summary_separates_all_events_from_candidates_and_path_metrics() -> None:
    candidates = pd.DataFrame({"event_id": ["e1"], "candidate_indicator": [True]})
    result = summarize_barriers(_events(), candidates, _paths())

    all_events = result.loc[result["scope"].eq("ALL_EVENTS")].iloc[0]
    candidate = result.loc[result["scope"].eq("CANDIDATES")].iloc[0]
    assert all_events["event_count"] == 2
    assert all_events["upper_touch_count"] == 1
    assert all_events["lower_touch_count"] == 1
    assert all_events["mean_time_to_touch_bars"] == pytest.approx(1.5)
    assert candidate["mean_mfe_log_return"] == pytest.approx(__import__("math").log(1.08))
    assert candidate["mean_mae_log_return"] == pytest.approx(__import__("math").log(1.01))
    assert candidate["mean_post_upper_continuation_log_return"] == pytest.approx(__import__("math").log(1.08 / 1.05))


def test_barrier_summary_rejects_unresolved_events() -> None:
    events = _events()
    events.loc[0, "target_status"] = "unresolved_tail"
    with pytest.raises(ValueError, match="unresolved"):
        summarize_barriers(events, pd.DataFrame({"event_id": ["e1"]}), _paths())
