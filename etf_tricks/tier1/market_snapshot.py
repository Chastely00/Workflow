from __future__ import annotations

import numpy as np
import pandas as pd
import json
from pathlib import Path


class ExecutionMarketSnapshot:
    """Derive an executable ETF raw-open proxy from prior realized holdings."""

    @staticmethod
    def read_canonical(data_store: str | Path, years: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
        root = Path(data_store)
        manifests: dict[str, dict[str, object]] = {}
        for artifact in ("daily_price_volume", "daily_market_state"):
            path = root / "manifests" / f"{artifact}.json"
            if not path.exists():
                raise ValueError(f"missing manifest: {artifact}")
            manifests[artifact] = json.loads(path.read_text(encoding="utf-8"))
            if manifests[artifact].get("artifact_id") != artifact:
                raise ValueError(f"invalid manifest identity: {artifact}")
        def load(artifact: str) -> pd.DataFrame:
            allowed = {str(item) for item in manifests[artifact].get("artifact_paths", [])}
            paths = [item for item in allowed if any(f"year={year}/" in item for year in years)]
            if not paths:
                raise ValueError(f"manifest has no requested partitions: {artifact}")
            return pd.concat([pd.read_parquet(root / item) for item in sorted(paths)], ignore_index=True)
        return load("daily_price_volume"), load("daily_market_state")

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
    def from_frames(holdings: pd.DataFrame, prices: pd.DataFrame, daily_nav: pd.DataFrame) -> pd.DataFrame:
        required_holdings = {"date", "etf_id", "ticker", "actual_weight"}
        required_prices = {"date", "ticker", "open", "previous_close", "source_available_at", "is_legal_execution"}
        if missing := required_holdings.difference(holdings.columns):
            raise ValueError(f"holdings missing columns: {sorted(missing)}")
        if missing := required_prices.difference(prices.columns):
            raise ValueError(f"prices missing columns: {sorted(missing)}")
        if missing := {"date", "etf_id", "nav"}.difference(daily_nav.columns):
            raise ValueError(f"daily_nav missing columns: {sorted(missing)}")
        h = holdings.copy()
        p = prices.copy()
        n = daily_nav.copy()
        h["date"] = pd.to_datetime(h["date"]).dt.normalize()
        p["date"] = pd.to_datetime(p["date"]).dt.normalize()
        n["date"] = pd.to_datetime(n["date"]).dt.normalize()
        p["source_available_at"] = pd.to_datetime(p["source_available_at"])
        if h.duplicated(["etf_id", "date", "ticker"]).any():
            raise ValueError("holdings has duplicate etf/date/ticker keys")
        if p.duplicated(["date", "ticker"]).any():
            raise ValueError("prices has duplicate date/ticker keys")
        if n.duplicated(["etf_id", "date"]).any():
            raise ValueError("daily_nav has duplicate etf/date keys")

        # Resolve one prior holdings date per ETF/market date first.  The prior
        # implementation re-scanned every holdings row for every market date;
        # this is equivalent but has O(ETF × dates) as-of joins instead.
        market_dates = p[["date"]].drop_duplicates().sort_values("date", kind="stable")
        holding_dates = h[["etf_id", "date"]].drop_duplicates().sort_values(["etf_id", "date"], kind="stable")
        anchors: list[pd.DataFrame] = []
        for etf_id, dates in holding_dates.groupby("etf_id", sort=False):
            lookup = pd.merge_asof(
                market_dates,
                dates.rename(columns={"date": "holding_as_of"}).sort_values("holding_as_of", kind="stable"),
                left_on="date",
                right_on="holding_as_of",
                direction="backward",
                allow_exact_matches=False,
            )
            lookup["etf_id"] = etf_id
            anchors.append(lookup)
        if not anchors:
            return pd.DataFrame(columns=["etf_id", "date", "raw_open_nav", "available_at", "is_legal_execution", "holding_as_of"])
        anchor_dates = pd.concat(anchors, ignore_index=True).dropna(subset=["holding_as_of"])
        joined = anchor_dates.merge(
            h.rename(columns={"date": "holding_as_of"}),
            on=["etf_id", "holding_as_of"],
            how="left",
            validate="one_to_many",
        ).merge(
            p,
            on=["date", "ticker"],
            how="left",
            validate="many_to_one",
        ).merge(
            n.rename(columns={"date": "holding_as_of", "nav": "anchor_nav"}),
            on=["etf_id", "holding_as_of"],
            how="left",
            validate="many_to_one",
        )
        weight = pd.to_numeric(joined["actual_weight"], errors="coerce")
        is_constituent_legal = (
            weight.notna()
            & joined["is_legal_execution"].eq(True)
            & pd.to_numeric(joined["open"], errors="coerce").gt(0)
            & pd.to_numeric(joined["previous_close"], errors="coerce").gt(0)
        )
        joined["_weight"] = weight
        joined["_open_factor"] = weight * pd.to_numeric(joined["open"], errors="coerce") / pd.to_numeric(joined["previous_close"], errors="coerce")
        joined["_is_constituent_legal"] = is_constituent_legal
        grouped = joined.groupby(["etf_id", "date", "holding_as_of"], sort=False, as_index=False).agg(
            weight_sum=("_weight", "sum"),
            open_factor=("_open_factor", "sum"),
            all_constituents_legal=("_is_constituent_legal", "all"),
            available_at=("source_available_at", "max"),
            anchor_nav=("anchor_nav", "first"),
        )
        valid = (
            np.isclose(grouped["weight_sum"], 1.0)
            & grouped["all_constituents_legal"]
            & pd.to_numeric(grouped["anchor_nav"], errors="coerce").gt(0)
        )
        grouped["is_legal_execution"] = valid
        grouped["raw_open_nav"] = np.where(valid, grouped["open_factor"] * grouped["anchor_nav"], np.nan)
        grouped.loc[~valid, "available_at"] = pd.NaT
        return grouped[["etf_id", "date", "raw_open_nav", "available_at", "is_legal_execution", "holding_as_of"]].sort_values(["etf_id", "date"], kind="stable").reset_index(drop=True)
