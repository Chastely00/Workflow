from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .models import CostPolicy


_ONE_NTD = Decimal("1")


@dataclass(frozen=True)
class CostBreakdown:
    notional: Decimal
    commission: Decimal
    tax: Decimal

    @property
    def total(self) -> Decimal:
        return self.commission + self.tax


def round_ntd(value: Decimal) -> Decimal:
    return value.quantize(_ONE_NTD, rounding=ROUND_HALF_UP)


def transaction_cost(
    side: str,
    shares: int,
    close: Decimal,
    policy: CostPolicy,
) -> CostBreakdown:
    if side not in {"buy", "sell"}:
        raise ValueError(f"invalid transaction side: {side!r}")
    if isinstance(shares, bool) or not isinstance(shares, int) or shares < 0:
        raise ValueError("shares must be a non-negative integer")
    price = Decimal(close)
    if not price.is_finite() or price <= 0:
        raise ValueError("close must be finite and positive")
    if shares == 0:
        return CostBreakdown(Decimal("0"), Decimal("0"), Decimal("0"))

    notional = Decimal(shares) * price
    commission = max(
        policy.minimum_commission,
        round_ntd(notional * policy.commission_rate),
    )
    tax = (
        round_ntd(notional * policy.sell_tax_rate)
        if side == "sell"
        else Decimal("0")
    )
    return CostBreakdown(notional, commission, tax)
