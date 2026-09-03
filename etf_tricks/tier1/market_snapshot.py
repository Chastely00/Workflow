from __future__ import annotations

import numpy as np
import pandas as pd


class ExecutionMarketSnapshot:
    """Derive an executable ETF raw-open proxy from prior realized holdings."""

    @staticmethod
    def prepare_prices(prices: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
        required_prices = {"date", "ticker", "open", "close"}
        required_states = {"date", "ticker", "market_state", "exchange_tradable", "source_available_date"}
        if missing := required_prices.difference(prices.columns):
            raise ValueError(f"prices missing columns: {sorted(missing)}")
        if missing := required_states.difference(states.columns):
            raise ValueError(f"states missing columns: {sorted(missing)}")
        p = prices.copy()
        s = states.copy()
        for frame in (p, s):
            frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
            frame["ticker"] = frame["ticker"].astype(str)
        p = p.sort_values(["ticker", "date"], kind="stable")
        p["previous_close"] = p.groupby("ticker", sort=False)["close"].shift(1)
        merged = p.merge(s, on=["date", "ticker"], how="inner", validate="one_to_one")
        merged["is_legal_execution"] = merged["market_state"].eq("TRADING") & merged["exchange_tradable"].eq(True)
        merged["source_available_at"] = pd.to_datetime(merged["source_available_date"])
        return merged[["date", "ticker", "open", "previous_close", "source_available_at", "is_legal_execution"]]

    @staticmethod
    def from_frames(holdings: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
        required_holdings = {"date", "etf_id", "ticker", "actual_weight"}
        required_prices = {"date", "ticker", "open", "previous_close", "source_available_at", "is_legal_execution"}
        if missing := required_holdings.difference(holdings.columns):
            raise ValueError(f"holdings missing columns: {sorted(missing)}")
        if missing := required_prices.difference(prices.columns):
            raise ValueError(f"prices missing columns: {sorted(missing)}")
        h = holdings.copy()
        p = prices.copy()
        h["date"] = pd.to_datetime(h["date"]).dt.normalize()
        p["date"] = pd.to_datetime(p["date"]).dt.normalize()
        p["source_available_at"] = pd.to_datetime(p["source_available_at"])
        rows: list[dict[str, object]] = []
        for date, date_prices in p.groupby("date", sort=True):
            previous = h[h["date"].lt(date)]
            for etf_id, etf_holdings in previous.groupby("etf_id", sort=False):
                latest = etf_holdings[etf_holdings["date"].eq(etf_holdings["date"].max())]
                joined = latest.merge(date_prices, on="ticker", how="left", validate="one_to_one")
                weight = pd.to_numeric(joined["actual_weight"], errors="coerce")
                valid = (
                    weight.notna().all()
                    and np.isclose(float(weight.sum()), 1.0)
                    and joined["is_legal_execution"].eq(True).all()
                    and pd.to_numeric(joined["open"], errors="coerce").gt(0).all()
                    and pd.to_numeric(joined["previous_close"], errors="coerce").gt(0).all()
                )
                raw_open_nav = np.nan
                available_at = pd.NaT
                if valid:
                    raw_open_nav = float((weight * joined["open"] / joined["previous_close"]).sum() * 100.0)
                    available_at = joined["source_available_at"].max()
                rows.append({"etf_id": etf_id, "date": date, "raw_open_nav": raw_open_nav, "available_at": available_at, "is_legal_execution": bool(valid), "holding_as_of": latest["date"].iloc[0]})
        return pd.DataFrame(rows).sort_values(["etf_id", "date"], kind="stable").reset_index(drop=True)
