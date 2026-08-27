from __future__ import annotations

import pandas as pd
import pandas.testing as pdt
import pytest

from etf_tricks.calendar import TradingCalendar
from etf_tricks.registry import get_etf_spec
from etf_tricks.universe import UniverseEngine


FORMATION = pd.Timestamp("2025-01-31")


def _calendar() -> TradingCalendar:
    dates = pd.bdate_range(end=FORMATION, periods=30)
    return TradingCalendar(
        pd.DataFrame(
            {"date": dates, "market": "TWSE", "is_trading_day": True}
        )
    )


def _features(tickers: list[str], *, signal: str = "momentum_12_1") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "formation_date": FORMATION,
            "ticker": tickers,
            "close": 50.0,
            "adv20": [100.0 - index for index in range(len(tickers))],
            "stock_traded_value_sum20": 2.5,
            "adv20_observation_count": 20,
            "market_cap": [1_000.0 - index for index in range(len(tickers))],
            signal: [10.0 - index for index in range(len(tickers))],
        }
    )


def _master(tickers: list[str], *, industries: list[str] | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": tickers,
            "stock_name": [f"Stock {ticker}" for ticker in tickers],
            "list_date": pd.Timestamp("2020-01-01"),
            "delist_date": pd.NaT,
            "main_industry": industries or ["M1100 Cement Industry"] * len(tickers),
        }
    )


def _ix0001(total: float = 1_000.0) -> pd.DataFrame:
    dates = pd.bdate_range(end=FORMATION, periods=20)
    return pd.DataFrame({"date": dates, "ticker": "IX0001", "amt": total / 20})


def _select(
    etf_id: str,
    features: pd.DataFrame,
    security_master: pd.DataFrame,
    ix0001: pd.DataFrame | None = None,
):
    return UniverseEngine(_calendar()).select(
        get_etf_spec(etf_id),
        FORMATION,
        features,
        security_master,
        _ix0001() if ix0001 is None else ix0001,
    )


def test_listing_ticker_and_price_rules_are_explicitly_audited():
    tickers = ["1101", "0000", "12A4", "12345", "1102", "1103", "1104"]
    features = _features(tickers)
    features.loc[features["ticker"] == "1104", "close"] = float("nan")
    master = _master(tickers)
    master.loc[master["ticker"] == "1102", "list_date"] = pd.Timestamp("2025-02-01")
    master.loc[master["ticker"] == "1103", "delist_date"] = FORMATION

    result = _select("momentum", features, master)
    audit = result.candidates.set_index("ticker")

    assert result.targets["ticker"].tolist() == ["1101"]
    assert audit.loc["0000", "exclusion_reason"] == "invalid_common_stock_ticker"
    assert audit.loc["12A4", "exclusion_reason"] == "invalid_common_stock_ticker"
    assert audit.loc["12345", "exclusion_reason"] == "invalid_common_stock_ticker"
    assert audit.loc["1102", "exclusion_reason"] == "not_yet_listed"
    assert audit.loc["1103", "exclusion_reason"] == "delisted_on_or_before_formation"
    assert audit.loc["1104", "exclusion_reason"] == "missing_positive_raw_close"


def test_exact_sector_strings_and_roe_financial_exclusion_are_preserved():
    tickers = ["2801", "2891", "2601", "2633", "1101"]
    industries = [
        "M2800 Financial Industry",
        "OTC28 OTC Banking",
        "M2600 Shipping and Transportation",
        "OTC26 OTC Transporation",
        "M1100 Cement Industry",
    ]
    features = _features(tickers, signal="adv20")
    features["r103"] = 15.0
    master = _master(tickers, industries=industries)

    assert _select("financial", features, master).targets["ticker"].tolist() == ["2801", "2891"]
    assert _select("shipping", features, master).targets["ticker"].tolist() == ["2601", "2633"]
    roe = _select("roe", features, master)
    assert roe.targets["ticker"].tolist() == ["2601", "2633", "1101"]
    excluded = roe.candidates.set_index("ticker")
    assert excluded.loc["2801", "exclusion_reason"] == "excluded_industry"
    assert excluded.loc["2891", "exclusion_reason"] == "excluded_industry"


def test_general_liquidity_uses_preferred_pool_only_when_it_has_five_candidates():
    tickers = [str(1101 + index) for index in range(7)]
    features = _features(tickers)
    features["stock_traded_value_sum20"] = [2.5, 2.4, 2.3, 2.2, 2.1, 1.5, 0.9]

    preferred = _select("momentum", features, _master(tickers))
    assert preferred.liquidity_threshold == pytest.approx(0.002)
    assert preferred.targets["ticker"].tolist() == tickers[:5]
    assert preferred.candidates.set_index("ticker").loc[tickers[5], "exclusion_reason"] == "below_liquidity_threshold"

    features.loc[4, "stock_traded_value_sum20"] = 1.9
    fallback = _select("momentum", features, _master(tickers))
    assert fallback.liquidity_threshold == pytest.approx(0.001)
    assert fallback.targets["ticker"].tolist() == tickers[:6]


def test_sector_thresholds_are_fixed_and_index_window_must_be_complete():
    features = _features(["2801"], signal="adv20")
    master = _master(["2801"], industries=["M2800 Financial Industry"])
    features["stock_traded_value_sum20"] = 1.0
    assert _select("financial", features, master).liquidity_threshold == pytest.approx(0.001)

    shipping_features = _features(["2601"], signal="adv20")
    shipping_features["stock_traded_value_sum20"] = 0.5
    shipping_master = _master(["2601"], industries=["M2600 Shipping and Transportation"])
    assert _select("shipping", shipping_features, shipping_master).liquidity_threshold == pytest.approx(0.0005)

    incomplete_ix = _ix0001().iloc[1:].copy()
    with pytest.raises(ValueError, match="IX0001.*20"):
        _select("shipping", shipping_features, shipping_master, incomplete_ix)


def test_ranking_is_stable_and_weights_follow_only_the_declared_mode():
    tickers = [str(1101 + index) for index in range(12)]
    features = _features(tickers)
    features["momentum_12_1"] = 1.0
    features["adv20"] = 100.0
    features["market_cap"] = 1_000.0
    shuffled = features.sample(frac=1.0, random_state=7)

    equal = _select("momentum", shuffled, _master(tickers))
    assert equal.targets["ticker"].tolist() == sorted(tickers)[:10]
    assert equal.targets["target_weight"].tolist() == pytest.approx([0.1] * 10)

    market = _select("market_cap", features.iloc[:2], _master(tickers[:2]))
    expected = features.iloc[:2]["market_cap"] / features.iloc[:2]["market_cap"].sum()
    assert market.targets["target_weight"].tolist() == pytest.approx(expected.tolist())


def test_one_to_four_candidates_are_held_and_zero_requests_carry_forward():
    features = _features(["1101", "1102", "1103"])
    three = _select("momentum", features, _master(features["ticker"].tolist()))
    assert len(three.targets) == 3
    assert three.carry_forward is False

    features["momentum_12_1"] = float("nan")
    zero = _select("momentum", features, _master(features["ticker"].tolist()))
    assert zero.targets.empty
    assert zero.carry_forward is True
    assert set(zero.candidates["exclusion_reason"]) == {"invalid_signal"}


def test_stock_and_index_windows_cannot_be_misaligned_or_zero():
    features = _features(["1101"])
    features["adv20_observation_count"] = 19
    incomplete = _select("momentum", features, _master(["1101"]))
    assert incomplete.targets.empty
    assert incomplete.candidates.iloc[0]["exclusion_reason"] == "incomplete_stock_liquidity_window"

    ix = _ix0001(total=0.0)
    with pytest.raises(ValueError, match="positive"):
        _select("momentum", _features(["1101"]), _master(["1101"]), ix)


def test_prepared_formation_context_preserves_each_spec_selection():
    tickers = [str(1101 + index) for index in range(7)]
    features = _features(tickers)
    master = _master(tickers)
    engine = UniverseEngine(_calendar())
    context = engine.prepare(FORMATION, features, master, _ix0001())

    for etf_id in ("momentum", "market_cap"):
        spec = get_etf_spec(etf_id)
        expected = engine.select(spec, FORMATION, features, master, _ix0001())
        actual = engine.select_prepared(spec, context)
        assert actual.liquidity_threshold == expected.liquidity_threshold
        assert actual.carry_forward == expected.carry_forward
        pdt.assert_frame_equal(actual.candidates, expected.candidates)
        pdt.assert_frame_equal(actual.targets, expected.targets)
