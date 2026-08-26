from __future__ import annotations

import pytest

from etf_tricks.registry import ETF_IDS, get_etf_spec


EXPECTED_IDS = (
    "market_cap",
    "monthly_sales",
    "chip",
    "roe",
    "momentum",
    "low_volatility",
    "financial",
    "shipping",
    "volume_ratio",
    "traded_amount",
    "turnover",
    "sharpe_60d",
    "sortino_60d",
)


def test_registry_exposes_the_13_authoritative_etfs_in_order() -> None:
    assert ETF_IDS == EXPECTED_IDS
    assert len(set(ETF_IDS)) == 13


def test_only_market_cap_uses_market_cap_weighting() -> None:
    modes = {etf_id: get_etf_spec(etf_id).weighting for etf_id in ETF_IDS}

    assert modes["market_cap"] == "market_cap"
    assert {value for key, value in modes.items() if key != "market_cap"} == {"equal"}


def test_signal_directions_match_the_authoritative_contract() -> None:
    directions = {etf_id: get_etf_spec(etf_id).direction for etf_id in ETF_IDS}

    assert directions["low_volatility"] == "ascending"
    assert {value for key, value in directions.items() if key != "low_volatility"} == {
        "descending"
    }


def test_liquidity_policies_distinguish_general_financial_and_shipping() -> None:
    general = get_etf_spec("momentum").liquidity_policy
    financial = get_etf_spec("financial").liquidity_policy
    shipping = get_etf_spec("shipping").liquidity_policy

    assert (general.preferred_ratio, general.floor_ratio, general.adaptive) == (
        0.002,
        0.001,
        True,
    )
    assert (financial.preferred_ratio, financial.floor_ratio, financial.adaptive) == (
        0.001,
        0.001,
        False,
    )
    assert (shipping.preferred_ratio, shipping.floor_ratio, shipping.adaptive) == (
        0.0005,
        0.0005,
        False,
    )


def test_industry_contracts_are_exact_and_unknown_ids_fail() -> None:
    assert get_etf_spec("financial").industry_include == (
        "M2800 Financial Industry",
        "OTC28 OTC Banking",
    )
    assert get_etf_spec("shipping").industry_include == (
        "M2600 Shipping and Transportation",
        "OTC26 OTC Transporation",
    )
    assert get_etf_spec("roe").industry_exclude == (
        "M2800 Financial Industry",
        "OTC28 OTC Banking",
    )

    with pytest.raises(KeyError, match="unknown ETF ID"):
        get_etf_spec("not_an_etf")
