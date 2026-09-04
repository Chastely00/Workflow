import pandas as pd
import pytest

from etf_tricks.tier3.return_history import build_pit_daily_return_history


def _membership() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "etf_id": ["a", "a", "a", "b", "b", "b"],
            "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"] * 2),
            "nav": [100.0, 105.0, 103.0, 100.0, 101.0, 102.0],
            "source_available_at": pd.to_datetime(
                [
                    "2024-01-02 13:30+08:00", "2024-01-03 13:30+08:00", "2024-01-04 13:30+08:00",
                ] * 2
            ),
        }
    )


def test_return_history_uses_current_close_availability_and_prior_nav_only() -> None:
    history = build_pit_daily_return_history(_membership())

    assert history.to_dict("records") == [
        {
            "date": pd.Timestamp("2024-01-03"), "etf_id": "a", "daily_return": pytest.approx(0.05),
            "available_at": pd.Timestamp("2024-01-03 05:30+00:00"),
        },
        {
            "date": pd.Timestamp("2024-01-04"), "etf_id": "a", "daily_return": pytest.approx(-2 / 105),
            "available_at": pd.Timestamp("2024-01-04 05:30+00:00"),
        },
        {
            "date": pd.Timestamp("2024-01-03"), "etf_id": "b", "daily_return": pytest.approx(0.01),
            "available_at": pd.Timestamp("2024-01-03 05:30+00:00"),
        },
        {
            "date": pd.Timestamp("2024-01-04"), "etf_id": "b", "daily_return": pytest.approx(1 / 101),
            "available_at": pd.Timestamp("2024-01-04 05:30+00:00"),
        },
    ]


def test_return_history_rejects_duplicate_etf_date_and_invalid_nav() -> None:
    duplicate = pd.concat([_membership(), _membership().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate ETF-date"):
        build_pit_daily_return_history(duplicate)
    invalid = _membership()
    invalid.loc[0, "nav"] = 0
    with pytest.raises(ValueError, match="positive finite NAV"):
        build_pit_daily_return_history(invalid)


def test_future_append_does_not_change_historical_return_rows() -> None:
    base = build_pit_daily_return_history(_membership())
    future = pd.concat(
        [
            _membership(),
            pd.DataFrame(
                {
                    "etf_id": ["a", "b"],
                    "date": pd.to_datetime(["2024-01-05", "2024-01-05"]),
                    "nav": [110.0, 98.0],
                    "source_available_at": pd.to_datetime(["2024-01-05 13:30+08:00", "2024-01-05 13:30+08:00"]),
                }
            ),
        ],
        ignore_index=True,
    )
    appended = build_pit_daily_return_history(future)
    pd.testing.assert_frame_equal(base, appended.loc[appended["date"].lt("2024-01-05")].reset_index(drop=True))
