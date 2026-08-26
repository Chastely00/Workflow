from __future__ import annotations

from .models import ETFSpec, LiquidityPolicy


GENERAL_LIQUIDITY = LiquidityPolicy(0.002, 0.001, True)
FINANCIAL_LIQUIDITY = LiquidityPolicy(0.001, 0.001, False)
SHIPPING_LIQUIDITY = LiquidityPolicy(0.0005, 0.0005, False)

FINANCIAL_INDUSTRIES = (
    "M2800 Financial Industry",
    "OTC28 OTC Banking",
)
SHIPPING_INDUSTRIES = (
    "M2600 Shipping and Transportation",
    "OTC26 OTC Transporation",
)


_SPECS = (
    ETFSpec("market_cap", "市值", "market_cap", "descending", "market_cap", GENERAL_LIQUIDITY),
    ETFSpec("monthly_sales", "月營收", "r18", "descending", "equal", GENERAL_LIQUIDITY),
    ETFSpec("chip", "籌碼", "chip_20d", "descending", "equal", GENERAL_LIQUIDITY),
    ETFSpec(
        "roe",
        "ROE",
        "r103",
        "descending",
        "equal",
        GENERAL_LIQUIDITY,
        industry_exclude=FINANCIAL_INDUSTRIES,
    ),
    ETFSpec("momentum", "動能", "momentum_12_1", "descending", "equal", GENERAL_LIQUIDITY),
    ETFSpec(
        "low_volatility",
        "低波",
        "vol_60d",
        "ascending",
        "equal",
        GENERAL_LIQUIDITY,
    ),
    ETFSpec(
        "financial",
        "金融",
        "adv20",
        "descending",
        "equal",
        FINANCIAL_LIQUIDITY,
        industry_include=FINANCIAL_INDUSTRIES,
    ),
    ETFSpec(
        "shipping",
        "航運",
        "adv20",
        "descending",
        "equal",
        SHIPPING_LIQUIDITY,
        industry_include=SHIPPING_INDUSTRIES,
    ),
    ETFSpec("volume_ratio", "量能", "volume_ratio", "descending", "equal", GENERAL_LIQUIDITY),
    ETFSpec("traded_amount", "金額", "adv20", "descending", "equal", GENERAL_LIQUIDITY),
    ETFSpec("turnover", "週轉率", "turnover_20d", "descending", "equal", GENERAL_LIQUIDITY),
    ETFSpec("sharpe_60d", "近60日 Sharpe", "sharpe_60d", "descending", "equal", GENERAL_LIQUIDITY),
    ETFSpec("sortino_60d", "近60日 Sortino", "sortino_60d", "descending", "equal", GENERAL_LIQUIDITY),
)

ETF_IDS = tuple(spec.etf_id for spec in _SPECS)
_BY_ID = {spec.etf_id: spec for spec in _SPECS}


def get_etf_spec(etf_id: str) -> ETFSpec:
    try:
        return _BY_ID[etf_id]
    except KeyError as exc:
        raise KeyError(f"unknown ETF ID: {etf_id}") from exc

