from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


Direction = Literal["ascending", "descending"]
Weighting = Literal["equal", "market_cap"]


@dataclass(frozen=True)
class LiquidityPolicy:
    preferred_ratio: float
    floor_ratio: float
    adaptive: bool


@dataclass(frozen=True)
class ETFSpec:
    etf_id: str
    display_name: str
    signal_name: str
    direction: Direction
    weighting: Weighting
    liquidity_policy: LiquidityPolicy
    min_candidates: int = 5
    max_candidates: int = 10
    industry_include: tuple[str, ...] = ()
    industry_exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class CostPolicy:
    commission_rate: Decimal = Decimal("0.001425")
    sell_tax_rate: Decimal = Decimal("0.003")
    minimum_commission: Decimal = Decimal("1")


@dataclass(frozen=True)
class RunConfig:
    start_date: str
    end_date: str
    initial_capital: Decimal = Decimal("10000000")

