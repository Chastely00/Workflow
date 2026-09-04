"""PIT-safe assembly of Tier 1 targets from immutable input tables."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path

import pandas as pd

from .market_snapshot import ExecutionMarketSnapshot
from .targets import Tier1TargetBuilder, Tier1TargetConfig


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_target_metadata(
    *,
    afml_manifest_path: str | Path,
    etf_manifest_path: str | Path,
    price_manifest_path: str | Path,
    market_state_manifest_path: str | Path,
    start_date: str,
    end_date: str,
    config: Tier1TargetConfig,
) -> dict[str, object]:
    """Bind every immutable manifest and target rule into target metadata."""
    if pd.Timestamp(start_date).normalize() > pd.Timestamp(end_date).normalize():
        raise ValueError("start_date must not exceed end_date")
    paths = {
        "afml_manifest_sha256": afml_manifest_path,
        "etf_manifest_sha256": etf_manifest_path,
        "price_manifest_sha256": price_manifest_path,
        "market_state_manifest_sha256": market_state_manifest_path,
    }
    if missing := [str(path) for path in paths.values() if not Path(path).is_file()]:
        raise ValueError(f"required manifest is missing: {missing}")
    return {
        **{key: _sha256(path) for key, path in paths.items()},
        "requested_date_range": [str(pd.Timestamp(start_date).date()), str(pd.Timestamp(end_date).date())],
        "target_config": asdict(config),
        "execution_price_semantics": "raw_unadjusted_execution_prices",
        "daily_close_availability_source": "bar_daily_membership.member_available_at",
    }


def build_target_table(
    bars: pd.DataFrame,
    holdings: pd.DataFrame,
    prices: pd.DataFrame,
    states: pd.DataFrame,
    daily_nav: pd.DataFrame,
    daily_membership: pd.DataFrame,
    *,
    config: Tier1TargetConfig | None = None,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Build triple-barrier targets using prior holdings and raw open execution.

    ``daily_membership.member_available_at`` is the authoritative daily NAV
    availability timestamp.  It is joined by ETF/date rather than inferred
    from later bar completion, preserving the daily information clock.
    """
    required_nav = {"date", "etf_id", "nav"}
    required_membership = {"date", "etf_id", "member_available_at"}
    if missing := required_nav.difference(daily_nav.columns):
        raise ValueError(f"daily_nav missing columns: {sorted(missing)}")
    if missing := required_membership.difference(daily_membership.columns):
        raise ValueError(f"daily_membership missing columns: {sorted(missing)}")

    membership = daily_membership[["etf_id", "date", "member_available_at"]].copy()
    membership["date"] = pd.to_datetime(membership["date"]).dt.normalize()
    membership["member_available_at"] = pd.to_datetime(membership["member_available_at"])
    if membership.duplicated(["etf_id", "date"]).any():
        raise ValueError("daily_membership has duplicate etf_id-date keys")
    if membership["member_available_at"].isna().any():
        raise ValueError("daily_membership requires valid member_available_at")

    nav = daily_nav[["etf_id", "date", "nav"]].copy()
    nav["date"] = pd.to_datetime(nav["date"]).dt.normalize()
    if nav.duplicated(["etf_id", "date"]).any():
        raise ValueError("daily_nav has duplicate etf_id-date keys")
    daily_closes = nav.merge(
        membership,
        on=["etf_id", "date"],
        how="inner",
        validate="one_to_one",
    ).rename(columns={"member_available_at": "available_at"})
    if len(daily_closes) != len(nav):
        raise ValueError("daily_nav has no matching daily membership availability")

    prepared_prices = ExecutionMarketSnapshot.prepare_prices(prices, states)
    opens = ExecutionMarketSnapshot.from_frames(holdings, prepared_prices, nav)
    return Tier1TargetBuilder(config or Tier1TargetConfig()).build(
        bars,
        opens,
        daily_closes,
        event_start_date=start_date,
        event_end_date=end_date,
    )
