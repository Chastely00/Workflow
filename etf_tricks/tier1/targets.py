from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Tier1TargetConfig:
    volatility_span: int = 60
    min_obs: int = 20
    pt_mult: float = 2.0
    sl_mult: float = 2.0
    vertical_bars: int = 60
    buy_cost_rate: float = 0.0
    sell_cost_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.volatility_span <= 0 or self.min_obs <= 0 or self.min_obs > self.volatility_span:
            raise ValueError("invalid volatility configuration")
        if self.pt_mult <= 0 or self.sl_mult <= 0 or self.vertical_bars <= 0:
            raise ValueError("barrier configuration must be positive")
        if not 0 <= self.buy_cost_rate < 1 or not 0 <= self.sell_cost_rate < 1:
            raise ValueError("cost rates must be in [0, 1)")


class Tier1TargetBuilder:
    """Build capital-neutral raw-OPEN Tier 1 evidence without mutating AFML labels."""

    def __init__(self, config: Tier1TargetConfig) -> None:
        self.config = config

    def build(self, bars: pd.DataFrame, opens: pd.DataFrame) -> pd.DataFrame:
        required_bars = {"etf_id", "bar_id", "bar_end_date", "close_nav", "feature_available_at"}
        required_opens = {"etf_id", "date", "raw_open_nav", "available_at", "is_legal_execution"}
        if missing := required_bars.difference(bars.columns):
            raise ValueError(f"bars missing columns: {sorted(missing)}")
        if missing := required_opens.difference(opens.columns):
            raise ValueError(f"opens missing columns: {sorted(missing)}")
        frame = bars.copy().sort_values(["etf_id", "bar_id"], kind="stable")
        frame["bar_end_date"] = pd.to_datetime(frame["bar_end_date"]).dt.normalize()
        frame["close_nav"] = pd.to_numeric(frame["close_nav"], errors="coerce")
        frame["_log_return"] = frame.groupby("etf_id", sort=False)["close_nav"].transform(lambda x: np.log(x).diff())
        frame["target_volatility"] = frame.groupby("etf_id", sort=False)["_log_return"].transform(lambda x: x.ewm(span=self.config.volatility_span, adjust=False, min_periods=self.config.min_obs).std(bias=False))
        market = opens.copy()
        market["date"] = pd.to_datetime(market["date"]).dt.normalize()
        market["available_at"] = pd.to_datetime(market["available_at"])
        market = market[market["is_legal_execution"].eq(True)].sort_values(["etf_id", "date"], kind="stable")
        output: list[dict[str, object]] = []
        for etf_id, group in frame.groupby("etf_id", sort=False):
            indexed = group.reset_index(drop=True)
            etf_market = market[market["etf_id"].eq(etf_id)]
            for position, row in indexed.iterrows():
                event = {"event_id": f"{etf_id}-{int(row.bar_id)}", "etf_id": etf_id, "t0_bar_id": int(row.bar_id), "t0_date": row.bar_end_date, "target_volatility": row.target_volatility, "target_status": "unresolved_tail", "y_direction": np.nan, "trigger_type": pd.NA, "trigger_date": pd.NaT, "entry_date": pd.NaT, "entry_raw_open": np.nan, "exit_date": pd.NaT, "exit_raw_open": np.nan, "net_log_return": np.nan}
                if position + self.config.vertical_bars >= len(indexed):
                    output.append(event)
                    continue
                entry = etf_market[etf_market.date.gt(row.bar_end_date)].head(1)
                vertical_date = indexed.iloc[position + self.config.vertical_bars].bar_end_date
                sigma = row.target_volatility
                if entry.empty or not np.isfinite(sigma) or sigma <= 0:
                    event["target_status"] = "missing_execution_or_volatility"
                    output.append(event)
                    continue
                entry_price = float(entry.iloc[0].raw_open_nav)
                future = indexed.iloc[position + 1 : position + self.config.vertical_bars + 1]
                path_net = np.log(
                    (future["close_nav"].to_numpy(dtype=float) * (1 - self.config.sell_cost_rate))
                    / (entry_price * (1 + self.config.buy_cost_rate))
                )
                upper = self.config.pt_mult * float(sigma)
                lower = -self.config.sl_mult * float(sigma)
                touches = np.flatnonzero((path_net >= upper) | (path_net <= lower))
                if len(touches):
                    trigger = future.iloc[int(touches[0])]
                    trigger_type = "upper" if path_net[int(touches[0])] >= upper else "lower"
                    exit_after = pd.Timestamp(trigger.bar_end_date)
                    label = 1 if trigger_type == "upper" else -1
                else:
                    trigger = indexed.iloc[position + self.config.vertical_bars]
                    trigger_type = "vertical"
                    exit_after = vertical_date
                    label = None
                exit_row = etf_market[etf_market.date.gt(exit_after)].head(1)
                if exit_row.empty:
                    event["target_status"] = "missing_execution_or_volatility"
                    output.append(event)
                    continue
                exit_price = float(exit_row.iloc[0].raw_open_nav)
                net = math.log((exit_price * (1 - self.config.sell_cost_rate)) / (entry_price * (1 + self.config.buy_cost_rate)))
                event.update({"trigger_type": trigger_type, "trigger_date": trigger.bar_end_date, "entry_date": entry.iloc[0].date, "entry_raw_open": entry_price, "exit_date": exit_row.iloc[0].date, "exit_raw_open": exit_price, "net_log_return": net, "target_status": f"resolved_{trigger_type}", "y_direction": (1 if net > 0 else -1) if label is None else label})
                output.append(event)
        return pd.DataFrame(output)
