from __future__ import annotations

import math
import statistics

import numpy as np
import pandas as pd
import pandas.testing as pdt

from etf_tricks.calendar import TradingCalendar
from etf_tricks.features import PITFeatureEngine


def _panels() -> tuple[TradingCalendar, dict[str, pd.DataFrame], pd.Timestamp]:
    dates = pd.bdate_range(end="2025-08-29", periods=300)
    calendar = TradingCalendar(
        pd.DataFrame(
            {"date": dates, "market": "TWSE", "is_trading_day": True}
        )
    )
    records: list[dict[str, object]] = []
    chips: list[dict[str, object]] = []
    for ticker, offset in (("1101", 0.0), ("1102", 5.0)):
        for index, date in enumerate(dates):
            records.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "close": 100.0 + offset + index,
                    "adj_close": 100.0 + offset + index,
                    "volume": float(index + 1),
                    "traded_value": float((index + 1) * 1_000_000),
                    "turnover": float(index + 1) / 1000.0,
                    "market_cap": float((index + 1) * 10_000_000 + offset),
                }
            )
            if not (ticker == "1102" and date == dates[-5]):
                chips.append(
                    {
                        "date": date,
                        "ticker": ticker,
                        "qfii_examt": 1.0,
                        "fund_examt": 2.0,
                        "dlrp_examt": -0.5,
                    }
                )

    monthly_sales = pd.DataFrame(
        [
            {
                "ticker": "1101",
                "source_period_date": "2025-06-01",
                "source_available_date": "2025-07-10",
                "source_row_id": "old",
                "r18": 5.0,
            },
            {
                "ticker": "1101",
                "source_period_date": "2025-07-01",
                "source_available_date": "2025-08-10",
                "source_row_id": "current",
                "r18": 20.0,
            },
            {
                "ticker": "1101",
                "source_period_date": "2025-07-01",
                "source_available_date": "2025-09-01",
                "source_row_id": "future_revision",
                "r18": 999.0,
            },
        ]
    )
    financial = pd.DataFrame(
        [
            {
                "ticker": "1101",
                "no": "TTM",
                "merg": "Y",
                "curr": "NTD",
                "period_end_date": "2025-06-30",
                "source_available_date": "2025-08-14",
                "revision_date": "2025-08-14",
                "source_row_id": "current",
                "r103": 15.0,
            },
            {
                "ticker": "1101",
                "no": "TTM",
                "merg": "Y",
                "curr": "NTD",
                "period_end_date": "2025-06-30",
                "source_available_date": "2025-09-02",
                "revision_date": "2025-09-02",
                "source_row_id": "future_revision",
                "r103": 999.0,
            },
            {
                "ticker": "1102",
                "no": "Q",
                "merg": "Y",
                "curr": "NTD",
                "period_end_date": "2025-06-30",
                "source_available_date": "2025-08-14",
                "revision_date": "2025-08-14",
                "source_row_id": "wrong_basis",
                "r103": 30.0,
            },
        ]
    )
    return (
        calendar,
        {
            "daily_price_volume": pd.DataFrame(records),
            "daily_chip": pd.DataFrame(chips),
            "monthly_sales": monthly_sales,
            "financial_statement_raw": financial,
        },
        dates[-1],
    )


def test_daily_features_use_exact_authoritative_windows() -> None:
    calendar, panels, formation_date = _panels()

    row = (
        PITFeatureEngine(calendar, panels)
        .compute(formation_date)
        .set_index("ticker")
        .loc["1101"]
    )
    prices = np.arange(100.0, 400.0)
    returns = [prices[index] / prices[index - 1] - 1.0 for index in range(240, 300)]
    downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in returns) / 60)

    assert row["momentum_12_1"] == prices[-22] / prices[-253] - 1.0
    assert row["adv20"] == 290.5 * 1_000_000
    assert row["stock_traded_value_sum20"] == 5_810_000_000.0
    assert row["turnover_20d"] == 0.2905
    assert row["volume_ratio"] == 290.5 / 250.5
    assert row["chip_20d"] == 50.0
    assert row["return_60d_observation_count"] == 60
    assert row["vol_60d"] == statistics.stdev(returns) * math.sqrt(252)
    assert row["sharpe_60d"] == (
        statistics.mean(returns) / statistics.stdev(returns) * math.sqrt(252)
    )
    assert math.isnan(row["sortino_60d"])
    assert downside == 0.0


def test_pit_fundamentals_ignore_future_revisions_and_keep_audit_dates() -> None:
    calendar, panels, formation_date = _panels()

    row = (
        PITFeatureEngine(calendar, panels)
        .compute(formation_date)
        .set_index("ticker")
        .loc["1101"]
    )

    assert row["r18"] == 20.0
    assert row["r18_source_period_date"] == pd.Timestamp("2025-07-01")
    assert row["r18_source_available_date"] == pd.Timestamp("2025-08-10")
    assert row["r18_period_age_months"] == 1
    assert row["r103"] == 15.0
    assert row["r103_period_end_date"] == pd.Timestamp("2025-06-30")
    assert row["r103_source_available_date"] == pd.Timestamp("2025-08-14")
    assert row["r103_age_days"] == 60


def test_missing_chip_component_excludes_signal_without_zero_fill() -> None:
    calendar, panels, formation_date = _panels()

    row = (
        PITFeatureEngine(calendar, panels)
        .compute(formation_date)
        .set_index("ticker")
        .loc["1102"]
    )

    assert row["chip_20d_observation_count"] == 19
    assert math.isnan(row["chip_20d"])
    assert math.isnan(row["r103"])


def test_low_volatility_accepts_20_adjacent_returns_but_sharpe_requires_60() -> None:
    calendar, panels, formation_date = _panels()
    daily = panels["daily_price_volume"]
    ticker_mask = daily["ticker"].eq("1102")
    ticker_dates = daily.loc[ticker_mask, "date"].sort_values()
    remove_dates = set(ticker_dates.iloc[-61:-21])
    panels["daily_price_volume"] = daily[
        ~(ticker_mask & daily["date"].isin(remove_dates))
    ].copy()

    row = (
        PITFeatureEngine(calendar, panels)
        .compute(formation_date)
        .set_index("ticker")
        .loc["1102"]
    )

    assert row["return_60d_observation_count"] == 20
    assert math.isfinite(row["vol_60d"])
    assert math.isnan(row["sharpe_60d"])
    assert math.isnan(row["sortino_60d"])


def test_roe_revision_effective_after_formation_is_not_visible() -> None:
    calendar, panels, formation_date = _panels()
    mask = panels["financial_statement_raw"]["ticker"].eq("1101")
    panels["financial_statement_raw"].loc[mask, "revision_date"] = "2025-09-02"

    row = (
        PITFeatureEngine(calendar, panels)
        .compute(formation_date)
        .set_index("ticker")
        .loc["1101"]
    )

    assert math.isnan(row["r103"])


def test_post_formation_rows_and_input_order_cannot_change_output() -> None:
    calendar, panels, formation_date = _panels()
    without_future = {
        key: frame[
            ~(
                frame.get("source_available_date", pd.Series(index=frame.index, dtype=object))
                .astype(str)
                .str.startswith("2025-09")
            )
        ].copy()
        if "source_available_date" in frame.columns
        else frame.copy()
        for key, frame in panels.items()
    }
    shuffled = {
        key: frame.sample(frac=1.0, random_state=7).reset_index(drop=True)
        for key, frame in panels.items()
    }

    expected = PITFeatureEngine(calendar, without_future).compute(formation_date)
    actual = PITFeatureEngine(calendar, shuffled).compute(formation_date)

    pdt.assert_frame_equal(actual, expected)


def test_valid_calendar_formation_without_daily_rows_returns_typed_empty_frame():
    calendar, panels, _ = _panels()
    formation = calendar.days[0]
    panels["daily_price_volume"] = panels["daily_price_volume"][
        ~panels["daily_price_volume"]["date"].eq(formation)
    ]

    frame = PITFeatureEngine(calendar, panels).compute(formation)

    assert frame.empty
    assert {
        "formation_date",
        "ticker",
        "close",
        "market_cap",
        "adv20",
        "stock_traded_value_sum20",
        "r18",
        "r103",
    }.issubset(frame.columns)


def test_compute_many_builds_two_pit_feature_snapshots_and_ignores_nontrading_rows():
    calendar, panels, final_formation = _panels()
    prior_formation = pd.Timestamp(calendar.days[-2])
    nontrading_date = pd.Timestamp("2025-08-23")
    panels["daily_price_volume"] = pd.concat(
        [
            panels["daily_price_volume"],
            pd.DataFrame(
                [
                    {
                        "date": nontrading_date,
                        "ticker": "1101",
                        "close": 999_999.0,
                        "adj_close": 999_999.0,
                        "volume": 999_999.0,
                        "traded_value": 999_999_000_000.0,
                        "turnover": 999.0,
                        "market_cap": 999_999_000_000.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    panels["daily_chip"] = pd.concat(
        [
            panels["daily_chip"],
            pd.DataFrame(
                [
                    {
                        "date": nontrading_date,
                        "ticker": "1101",
                        "qfii_examt": 999_999.0,
                        "fund_examt": 999_999.0,
                        "dlrp_examt": 999_999.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )

    snapshots = PITFeatureEngine(calendar, panels).compute_many(
        (prior_formation, final_formation)
    )

    assert tuple(snapshots) == (prior_formation, final_formation)
    prior = snapshots[prior_formation].set_index("ticker")
    final = snapshots[final_formation].set_index("ticker")
    assert prior.index.tolist() == ["1101", "1102"]
    assert final.index.tolist() == ["1101", "1102"]
    assert prior.loc["1101", "adv20_observation_count"] == 20
    assert final.loc["1101", "adv20"] == 290.5 * 1_000_000
    assert final.loc["1101", "chip_20d"] == 50.0
    assert final.loc["1101", "momentum_recent_date"] == calendar.days[-22]
    assert final.loc["1101", "momentum_old_date"] == calendar.days[-253]
