from decimal import Decimal

import pytest

from etf_tricks.costs import transaction_cost
from etf_tricks.models import CostPolicy


def test_no_trade_has_no_minimum_commission():
    cost = transaction_cost("buy", 0, Decimal("100"), CostPolicy())
    assert cost.notional == Decimal("0")
    assert cost.commission == Decimal("0")
    assert cost.tax == Decimal("0")
    assert cost.total == Decimal("0")


def test_authoritative_commission_and_sell_tax_use_integer_ntd():
    buy = transaction_cost("buy", 1_000, Decimal("100"), CostPolicy())
    sell = transaction_cost("sell", 1_000, Decimal("100"), CostPolicy())

    assert buy.notional == Decimal("100000")
    assert buy.commission == Decimal("143")
    assert buy.tax == Decimal("0")
    assert sell.commission == Decimal("143")
    assert sell.tax == Decimal("300")


def test_nonzero_trade_has_one_dollar_minimum_commission():
    cost = transaction_cost("buy", 1, Decimal("1"), CostPolicy())
    assert cost.commission == Decimal("1")


def test_half_ntd_rounds_away_from_zero():
    policy = CostPolicy(
        commission_rate=Decimal("0.025"),
        sell_tax_rate=Decimal("0.005"),
        minimum_commission=Decimal("0"),
    )
    buy = transaction_cost("buy", 1, Decimal("100"), policy)
    sell = transaction_cost("sell", 1, Decimal("100"), policy)
    assert buy.commission == Decimal("3")
    assert sell.tax == Decimal("1")


@pytest.mark.parametrize("side", ["hold", "BUY", ""])
def test_invalid_side_fails_closed(side: str):
    with pytest.raises(ValueError, match="side"):
        transaction_cost(side, 1, Decimal("100"), CostPolicy())


def test_negative_shares_or_nonpositive_price_fail_closed():
    with pytest.raises(ValueError, match="shares"):
        transaction_cost("buy", -1, Decimal("100"), CostPolicy())
    with pytest.raises(ValueError, match="close"):
        transaction_cost("buy", 1, Decimal("0"), CostPolicy())
