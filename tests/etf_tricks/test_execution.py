from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import numpy as np
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
                "market_state": "TRADING",
                "exchange_tradable": True,
            }
            for date in dates
            for ticker in tickers
        ]
    )


def _state_market(dates: list[str], tickers: list[str], close: float = 100.0) -> pd.DataFrame:
    frame = _market(dates, tickers, close)
    frame["exchange_tradable"] = pd.Series([True] * len(frame), dtype=object)
    frame["amount_state"] = "OBSERVED"
    frame["full_delivery"] = False
    return frame


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


def test_prepared_market_has_independent_state_and_tradability_codes():
    dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
    market = _state_market(dates, ["1101"])
    market.loc[market["date"].eq(pd.Timestamp("2025-01-03")), "market_state"] = "HALTED"
    market.loc[market["date"].eq(pd.Timestamp("2025-01-03")), "exchange_tradable"] = False
    market.loc[market["date"].eq(pd.Timestamp("2025-01-06")), "market_state"] = "MISSING"
    market.loc[market["date"].eq(pd.Timestamp("2025-01-06")), "exchange_tradable"] = None

    prepared = PortfolioExecutionEngine.prepare_market(market)

    assert prepared.market_state.tolist() == [[1], [2], [3]]
    assert prepared.exchange_tradable.tolist() == [[1], [0], [-1]]


def test_prepared_market_accepts_arrow_style_numpy_boolean_tradability():
    market = _state_market(["2025-01-02"], ["1101"])
    market["exchange_tradable"] = pd.Series([np.bool_(True)], dtype=object)

    prepared = PortfolioExecutionEngine.prepare_market(market)

    assert prepared.exchange_tradable.tolist() == [[1]]


@pytest.mark.parametrize("missing_columns", [("market_state",), ("exchange_tradable",), ("market_state", "exchange_tradable")])
def test_raw_market_requires_explicit_state_and_tradability(
    missing_columns: tuple[str, ...],
):
    market = _market(["2025-01-02"], ["1101"]).drop(columns=list(missing_columns))

    with pytest.raises(ValueError, match="market_state.*exchange_tradable"):
        PortfolioExecutionEngine.prepare_market(market)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda market: replace(
                market, market_state=market.market_state.astype(np.float64)
            ),
            "market_state.*integer",
        ),
        (
            lambda market: replace(
                market, exchange_tradable=market.exchange_tradable.astype(bool)
            ),
            "exchange_tradable.*integer",
        ),
        (
            lambda market: replace(
                market, exchange_tradable=np.zeros_like(market.exchange_tradable)
            ),
            "state/tradability",
        ),
        (
            lambda market: replace(
                market, close=market.close[:, :0]
            ),
            "matrix shape",
        ),
        (
            lambda market: replace(
                market, traded_value=np.array([[np.inf]], dtype=np.float64)
            ),
            "traded_value",
        ),
        (
            lambda market: replace(
                market,
                date_positions={pd.Timestamp("2025-01-02"): 1},
            ),
            "date_positions",
        ),
    ],
)
def test_run_rejects_untrusted_prepared_market(
    mutate: object,
    error: str,
):
    dates = ["2025-01-02"]
    prepared = PortfolioExecutionEngine.prepare_market(_market(dates, ["1101"]))

    with pytest.raises(ValueError, match=error):
        PortfolioExecutionEngine().run(
            get_etf_spec("momentum"),
            _targets("2025-01", {"1101": 1.0}),
            mutate(prepared),
            _calendar(dates),
            Decimal("1000"),
        )


def test_missing_state_target_with_zero_desired_emits_one_deduplicated_diagnostic():
    dates = ["2025-01-02"]
    market = _state_market(dates, ["1101"])
    market["market_state"] = "MISSING"
    market["amount_state"] = "MISSING"
    market["exchange_tradable"] = None
    market[["close", "adj_close"]] = float("nan")
    engine = PortfolioExecutionEngine()
    targets = _targets("2025-01", {"1101": 1.0})
    calendar = _calendar(dates)

    raw = engine.run(get_etf_spec("momentum"), targets, market, calendar, Decimal("1000"))
    prepared = engine.run(
        get_etf_spec("momentum"),
        targets,
        engine.prepare_market(market),
        calendar,
        Decimal("1000"),
    )

    assert raw.diagnostics["diagnostic"].value_counts().to_dict() == {
        "missing_market_state": 1,
        "missing_target_formation_price": 1,
    }
    pdt.assert_frame_equal(prepared.diagnostics, raw.diagnostics)


def test_halted_zero_authorized_day_keeps_backlog_and_resumes_only_when_trading():
    dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
    market = _state_market(dates, ["1101"])
    halted = market["date"].eq(pd.Timestamp("2025-01-03"))
    market.loc[halted, "market_state"] = "HALTED"
    market.loc[halted, "amount_state"] = "ZERO_AUTHORIZED"
    market.loc[halted, "exchange_tradable"] = False
    market.loc[halted, "traded_value"] = 0.0
    market.loc[halted, ["close", "adj_close"]] = float("nan")

    result = PortfolioExecutionEngine().run(
        get_etf_spec("momentum"),
        _targets("2025-01", {"1101": 1.0}),
        market,
        _calendar(dates),
        Decimal("2000"),
    )

    halted_trade = result.trades[result.trades["date"].eq(pd.Timestamp("2025-01-03"))].iloc[0]
    resumed_trade = result.trades[result.trades["date"].eq(pd.Timestamp("2025-01-06"))].iloc[0]
    halted_holding = result.daily_holdings[
        result.daily_holdings["date"].eq(pd.Timestamp("2025-01-03"))
    ].iloc[0]
    assert halted_trade["executed_shares"] == 0
    assert halted_trade["unfilled_shares"] > 0
    assert resumed_trade["backlog_before"] == halted_trade["unfilled_shares"]
    assert resumed_trade["executed_shares"] > 0
    assert halted_holding["raw_close"] == pytest.approx(100.0)
    assert halted_holding["source_price_date"] == pd.Timestamp("2025-01-02")
    assert halted_holding["stale_price_days"] == 1


def test_halted_observed_amount_is_preserved_but_never_executable():
    dates = ["2025-01-02"]
    market = _state_market(dates, ["1101"])
    market["market_state"] = "HALTED"
    market["exchange_tradable"] = False
    market["traded_value"] = 1234.5

    prepared = PortfolioExecutionEngine.prepare_market(market)
    result = PortfolioExecutionEngine().run(
        get_etf_spec("momentum"),
        _targets("2025-01", {"1101": 1.0}),
        prepared,
        _calendar(dates),
        Decimal("1000"),
    )

    assert prepared.traded_value.tolist() == [[1234.5]]
    assert result.trades.iloc[0]["executed_shares"] == 0


def test_trading_full_delivery_remains_executable():
    dates = ["2025-01-02"]
    market = _state_market(dates, ["1101"])
    market["full_delivery"] = True

    result = PortfolioExecutionEngine().run(
        get_etf_spec("momentum"),
        _targets("2025-01", {"1101": 1.0}),
        market,
        _calendar(dates),
        Decimal("1000"),
    )

    assert result.trades.iloc[0]["executed_shares"] > 0


def test_missing_market_state_is_non_executable_with_distinct_diagnostic():
    dates = ["2025-01-02"]
    market = _state_market(dates, ["1101"])
    market["market_state"] = "MISSING"
    market["amount_state"] = "MISSING"
    market["exchange_tradable"] = None

    result = PortfolioExecutionEngine().run(
        get_etf_spec("momentum"),
        _targets("2025-01", {"1101": 1.0}),
        market,
        _calendar(dates),
        Decimal("1000"),
    )

    assert result.trades.iloc[0]["executed_shares"] == 0
    assert result.diagnostics["diagnostic"].tolist() == ["missing_market_state"]


def test_stateful_prepared_and_reordered_raw_market_produce_identical_ledgers():
    dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
    market = _state_market(dates, ["1101"])
    market.loc[market["date"].eq(pd.Timestamp("2025-01-03")), "market_state"] = "HALTED"
    market.loc[market["date"].eq(pd.Timestamp("2025-01-03")), "exchange_tradable"] = False
    engine = PortfolioExecutionEngine()
    spec = get_etf_spec("momentum")
    targets = _targets("2025-01", {"1101": 1.0})
    calendar = _calendar(dates)

    expected = engine.run(spec, targets, market.sample(frac=1.0, random_state=7), calendar, Decimal("2000"))
    actual = engine.run(spec, targets, engine.prepare_market(market), calendar, Decimal("2000"))

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


def test_weekend_delist_liquidates_once_at_last_raw_close_and_never_rebuys():
    dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-02-03"]
    targets = pd.concat(
        [
            _targets("2025-01", {"1101": 1.0}),
            _targets("2025-02", {"1101": 1.0}),
        ],
        ignore_index=True,
    )
    market = _market(dates, ["1101"])
    market.loc[
        market["date"].eq(pd.Timestamp("2025-01-03")),
        ["market_state", "exchange_tradable"],
    ] = ["HALTED", False]
    market.loc[
        market["date"].eq(pd.Timestamp("2025-01-06")), ["close", "adj_close"]
    ] = float("nan")
    security_master = pd.DataFrame(
        {"ticker": ["1101"], "delist_date": [pd.Timestamp("2025-01-04")]}
    )
    engine = PortfolioExecutionEngine()
    calendar = _calendar(dates)

    raw = engine.run(
        get_etf_spec("momentum"),
        targets,
        market,
        calendar,
        Decimal("1000"),
        security_master=security_master,
    )
    prepared = engine.run(
        get_etf_spec("momentum"),
        targets,
        engine.prepare_market(market),
        calendar,
        Decimal("1000"),
        security_master=security_master,
    )

    forced = raw.trades[raw.trades["is_forced_delist_liquidation"]]
    assert forced[["date", "ticker", "side", "executed_shares", "raw_close"]].to_dict(
        "records"
    ) == [
        {
            "date": pd.Timestamp("2025-01-06"),
            "ticker": "1101",
            "side": "sell",
            "executed_shares": -3,
            "raw_close": 100.0,
        }
    ]
    assert forced.iloc[0]["commission"] == pytest.approx(1.0)
    assert forced.iloc[0]["tax"] == pytest.approx(1.0)
    halted_backlog = raw.trades[
        raw.trades["date"].eq(pd.Timestamp("2025-01-03"))
        & raw.trades["ticker"].eq("1101")
    ].iloc[0]
    assert halted_backlog["executed_shares"] == 0
    assert halted_backlog["unfilled_shares"] == 4
    assert raw.trades[raw.trades["ticker"].eq("1101")].shape[0] == 3
    assert raw.daily_holdings.loc[
        raw.daily_holdings["date"].ge(pd.Timestamp("2025-01-06"))
        & raw.daily_holdings["ticker"].eq("1101")
    ].empty
    pdt.assert_frame_equal(prepared.daily_etf, raw.daily_etf)
    pdt.assert_frame_equal(prepared.daily_holdings, raw.daily_holdings)
    pdt.assert_frame_equal(prepared.trades, raw.trades)
    pdt.assert_frame_equal(prepared.diagnostics, raw.diagnostics)


def test_lifecycle_rows_beyond_calendar_or_duplicate_ticker_fail_closed():
    dates = ["2025-01-02", "2025-01-03"]
    engine = PortfolioExecutionEngine()
    arguments = (
        get_etf_spec("momentum"),
        _targets("2025-01", {"1101": 1.0}),
        _market(dates, ["1101"]),
        _calendar(dates),
        Decimal("1000"),
    )

    with pytest.raises(ValueError, match="outside.*calendar"):
        engine.run(
            *arguments,
            security_master=pd.DataFrame(
                {"ticker": ["1101"], "delist_date": [pd.Timestamp("2025-01-06")]}
            ),
        )
    with pytest.raises(ValueError, match="duplicate"):
        engine.run(
            *arguments,
            security_master=pd.DataFrame(
                {
                    "ticker": ["1101", "1101"],
                    "delist_date": [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
                }
            ),
        )


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
    market["market_state"] = "TRADING"
    market["exchange_tradable"] = True

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
