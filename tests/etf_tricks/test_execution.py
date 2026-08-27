from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pandas.testing as pdt
import pytest

from etf_tricks.calendar import TradingCalendar
from etf_tricks.execution import (
    PreparedExecutionMarket,
    PortfolioExecutionEngine,
    apply_synthetic_corporate_action,
    round_half_away_from_zero,
    scheduled_position,
)
from etf_tricks.registry import get_etf_spec


def _calendar(dates: list[str]) -> TradingCalendar:
    return TradingCalendar(
        pd.DataFrame(
            {"date": pd.to_datetime(dates), "market": "TWSE", "is_trading_day": True}
        )
    )


def _market(dates: list[str], tickers: list[str], close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(date),
                "ticker": ticker,
                "close": close,
                "adj_close": close,
                "traded_value": 1_000_000.0,
            }
            for date in dates
            for ticker in tickers
        ]
    )


def _targets(month: str, weights: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "formation_date": pd.Timestamp(month) - pd.Timedelta(days=1),
                "target_month": month,
                "etf_id": "momentum",
                "ticker": ticker,
                "target_weight": weight,
            }
            for ticker, weight in weights.items()
        ]
    )


def test_rounding_and_variable_month_schedule_are_exact():
    assert round_half_away_from_zero(Decimal("2.5")) == 3
    assert round_half_away_from_zero(Decimal("-2.5")) == -3
    assert [scheduled_position(0, 10, k, 3) for k in (1, 2, 3)] == [3, 7, 10]
    assert [scheduled_position(10, 0, k, 3) for k in (1, 2, 3)] == [7, 3, 0]


def test_hand_checkable_three_day_self_financing_ledger():
    dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
    result = PortfolioExecutionEngine().run(
        get_etf_spec("momentum"),
        _targets("2025-01", {"1101": 0.5, "1102": 0.5}),
        _market(dates, ["1101", "1102"]),
        _calendar(dates),
        Decimal("1000"),
    )

    daily = result.daily_etf
    assert daily["cash"].tolist() == pytest.approx([598.0, 396.0, 94.0])
    assert daily["commission"].tolist() == pytest.approx([2.0, 2.0, 2.0])
    assert daily["tax"].tolist() == pytest.approx([0.0, 0.0, 0.0])
    assert daily["total_assets"].tolist() == pytest.approx([998.0, 996.0, 994.0])
    assert daily["nav"].tolist() == pytest.approx([99.8, 99.6, 99.4])
    assert daily["target_completion_ratio"].tolist() == pytest.approx([0.4, 0.6, 0.9])
    assert (daily["cash"] >= 0).all()

    holdings = result.daily_holdings.pivot(index="date", columns="ticker", values="shares")
    assert holdings["1101"].tolist() == [2, 3, 5]
    assert holdings["1102"].tolist() == [2, 3, 4]

    last_trades = result.trades[result.trades["date"].eq(pd.Timestamp(dates[-1]))]
    assert last_trades.set_index("ticker")["executed_shares"].to_dict() == {
        "1101": 2,
        "1102": 1,
    }
    assert last_trades.set_index("ticker")["unfilled_shares"].to_dict() == {
        "1101": 0,
        "1102": 1,
    }


def test_prepared_market_preserves_the_exact_hand_ledger():
    dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
    engine = PortfolioExecutionEngine()
    spec = get_etf_spec("momentum")
    targets = _targets("2025-01", {"1101": 0.5, "1102": 0.5})
    market = _market(dates, ["1101", "1102"])
    calendar = _calendar(dates)

    expected = engine.run(spec, targets, market, calendar, Decimal("1000"))
    actual = engine.run(
        spec,
        targets,
        engine.prepare_market(market),
        calendar,
        Decimal("1000"),
    )

    pdt.assert_frame_equal(actual.daily_etf, expected.daily_etf)
    pdt.assert_frame_equal(actual.daily_holdings, expected.daily_holdings)
    pdt.assert_frame_equal(actual.trades, expected.trades)
    pdt.assert_frame_equal(actual.diagnostics, expected.diagnostics)


def test_next_month_starts_from_actual_holdings_and_sells_before_buys():
    dates = [
        "2025-01-02",
        "2025-01-03",
        "2025-01-06",
        "2025-02-03",
        "2025-02-04",
    ]
    targets = pd.concat(
        [
            _targets("2025-01", {"1101": 1.0}),
            _targets("2025-02", {"1102": 1.0}),
        ],
        ignore_index=True,
    )
    result = PortfolioExecutionEngine().run(
        get_etf_spec("momentum"),
        targets,
        _market(dates, ["1101", "1102"]),
        _calendar(dates),
        Decimal("1000"),
    )

    feb_first = result.trades[result.trades["date"].eq(pd.Timestamp("2025-02-03"))]
    assert feb_first["side"].tolist() == ["sell", "buy"]
    assert feb_first.set_index("ticker")["executed_shares"].to_dict() == {
        "1101": -5,
        "1102": 5,
    }
    final = result.daily_holdings[result.daily_holdings["date"].eq(pd.Timestamp(dates[-1]))]
    assert final.set_index("ticker")["shares"].to_dict() == {"1102": 9}
    assert result.daily_etf.iloc[-1]["cash"] >= 0


def test_fully_exited_ticker_is_not_looked_up_after_its_schedule_month(monkeypatch):
    dates = [
        "2025-01-31",
        "2025-02-03",
        "2025-02-04",
        "2025-03-03",
        "2025-03-04",
    ]
    targets = pd.concat(
        [
            _targets("2025-01", {"1101": 1.0}),
            _targets("2025-02", {"1102": 1.0}),
        ],
        ignore_index=True,
    )
    engine = PortfolioExecutionEngine()
    prepared = engine.prepare_market(_market(dates, ["1101", "1102"], close=10.0))
    calls: list[tuple[pd.Timestamp, str]] = []
    original = PreparedExecutionMarket.lookup

    def recording_lookup(self, date, ticker):
        calls.append((pd.Timestamp(date), str(ticker)))
        return original(self, date, ticker)

    monkeypatch.setattr(PreparedExecutionMarket, "lookup", recording_lookup)

    result = engine.run(
        get_etf_spec("momentum"),
        targets,
        prepared,
        _calendar(dates),
        Decimal("1000"),
    )

    march_calls = [ticker for date, ticker in calls if date.month == 3]
    assert "1102" in march_calls
    assert "1101" not in march_calls
    final = result.daily_holdings[result.daily_holdings["date"].eq(pd.Timestamp(dates[-1]))]
    assert final.set_index("ticker")["shares"].to_dict() == {"1102": 99}


def test_synthetic_corporate_action_converts_integer_shares_and_fraction_to_cash():
    conversion = apply_synthetic_corporate_action(
        shares=3,
        previous_close=Decimal("100"),
        previous_adj_close=Decimal("100"),
        current_close=Decimal("80"),
        current_adj_close=Decimal("100"),
    )
    assert conversion.multiplier == Decimal("1.25")
    assert conversion.shares == 3
    assert conversion.cash == Decimal("60.00")


def test_synthetic_corporate_action_scales_live_schedule_without_strategy_trade():
    dates = ["2025-01-02", "2025-01-03"]
    market = _market(dates, ["1101"])
    market.loc[market["date"].eq(pd.Timestamp("2025-01-03")), "close"] = 50.0

    result = PortfolioExecutionEngine().run(
        get_etf_spec("momentum"),
        _targets("2025-01", {"1101": 1.0}),
        market,
        _calendar(dates),
        Decimal("1000"),
    )

    holdings = result.daily_holdings.set_index("date")
    assert holdings.loc[pd.Timestamp("2025-01-02"), "shares"] == 5
    assert holdings.loc[pd.Timestamp("2025-01-03"), "synthetic_ca_multiplier"] == pytest.approx(2.0)
    assert holdings.loc[pd.Timestamp("2025-01-03"), "synthetic_ca_share_delta"] == 5
    assert holdings.loc[pd.Timestamp("2025-01-03"), "shares"] == 19
    second_day_trade = result.trades[result.trades["date"].eq(pd.Timestamp("2025-01-03"))].iloc[0]
    assert second_day_trade["side"] == "buy"
    assert second_day_trade["executed_shares"] == 9
    assert second_day_trade["synthetic_ca_share_delta"] == 5


def test_missing_price_carries_last_value_and_delisting_forces_settlement():
    dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
    market = _market(dates, ["1101"])
    market.loc[market["date"].eq(pd.Timestamp("2025-01-03")), ["close", "adj_close"]] = float("nan")
    security_master = pd.DataFrame(
        {"ticker": ["1101"], "delist_date": [pd.Timestamp("2025-01-06")]}
    )
    result = PortfolioExecutionEngine().run(
        get_etf_spec("momentum"),
        _targets("2025-01", {"1101": 1.0}),
        market,
        _calendar(dates),
        Decimal("1000"),
        security_master=security_master,
    )

    stale = result.daily_holdings[
        result.daily_holdings["date"].eq(pd.Timestamp("2025-01-03"))
    ].iloc[0]
    assert stale["raw_close"] == pytest.approx(100.0)
    assert stale["stale_price_days"] == 1
    assert stale["source_price_date"] == pd.Timestamp("2025-01-02")
    forced = result.trades[result.trades["is_forced_delist_liquidation"]]
    assert len(forced) == 1
    assert forced.iloc[0]["side"] == "sell"
    assert result.daily_etf.iloc[-1]["holdings_count"] == 0


def test_duplicate_market_keys_and_negative_capital_fail_closed():
    dates = ["2025-01-02"]
    market = _market(dates, ["1101"])
    with pytest.raises(ValueError, match="duplicate"):
        PortfolioExecutionEngine().run(
            get_etf_spec("momentum"),
            _targets("2025-01", {"1101": 1.0}),
            pd.concat([market, market], ignore_index=True),
            _calendar(dates),
            Decimal("1000"),
        )


def test_rotation_keeps_one_share_when_priced_target_is_unaffordable_after_fees():
    dates = ["2025-01-31", "2025-02-03"]
    targets = pd.concat(
        [
            _targets("2025-01", {"1101": 1.0}),
            _targets("2025-02", {"1102": 1.0}),
        ],
        ignore_index=True,
    )
    market = pd.DataFrame(
        [
            {"date": dates[0], "ticker": "1101", "close": 10.0, "adj_close": 10.0, "traded_value": 1_000.0},
            {"date": dates[0], "ticker": "1102", "close": 100.0, "adj_close": 100.0, "traded_value": 1_000.0},
            {"date": dates[1], "ticker": "1101", "close": 10.0, "adj_close": 10.0, "traded_value": 1_000.0},
            {"date": dates[1], "ticker": "1102", "close": 100.0, "adj_close": 100.0, "traded_value": 1_000.0},
        ]
    )

    result = PortfolioExecutionEngine().run(
        get_etf_spec("momentum"),
        targets,
        market,
        _calendar(dates),
        Decimal("101"),
    )

    final = result.daily_holdings[result.daily_holdings["date"].eq(pd.Timestamp(dates[1]))]
    assert final.set_index("ticker")["shares"].to_dict() == {"1101": 1}
    assert result.daily_etf.iloc[-1]["holdings_count"] == 1
    feb = result.trades[result.trades["date"].eq(pd.Timestamp(dates[1]))].set_index("ticker")
    assert feb.loc["1101", "executed_shares"] == -9
    assert feb.loc["1102", "executed_shares"] == 0
    with pytest.raises(ValueError, match="initial_capital"):
        PortfolioExecutionEngine().run(
            get_etf_spec("momentum"),
            _targets("2025-01", {"1101": 1.0}),
            market,
            _calendar(dates),
            Decimal("-1"),
        )
