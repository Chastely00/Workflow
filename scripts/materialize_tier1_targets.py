"""Materialize immutable, cost-audited Tier 1 triple-barrier targets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.tier1.artifact import write_target_artifact
from etf_tricks.tier1.market_snapshot import ExecutionMarketSnapshot
from etf_tricks.tier1.target_materializer import build_target_metadata, build_target_table
from etf_tricks.tier1.targets import Tier1TargetConfig


def _read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read JSON: {path}") from error
    if not isinstance(data, dict):
        raise ValueError(f"JSON object required: {path}")
    return data


def _validate_ready_manifest(path: Path, artifact_id: str) -> dict[str, object]:
    manifest = _read_json(path)
    if manifest.get("artifact_id") != artifact_id or manifest.get("status") != "ready":
        raise ValueError(f"invalid ready manifest identity: {artifact_id}")
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--etf-result-root", required=True)
    parser.add_argument("--data-store-root", default="DataAnalysts/data_store")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--volatility-span", type=int, default=60)
    parser.add_argument("--min-obs", type=int, default=20)
    parser.add_argument("--pt-mult", type=float, default=2.0)
    parser.add_argument("--sl-mult", type=float, default=2.0)
    parser.add_argument("--vertical-bars", type=int, default=60)
    parser.add_argument("--buy-cost-rate", type=float, default=0.001425)
    parser.add_argument("--sell-cost-rate", type=float, default=0.003)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    afml_root = Path(args.afml_root)
    etf_root = Path(args.etf_result_root)
    data_store = Path(args.data_store_root)
    output_root = Path(args.output_root)
    start = pd.Timestamp(args.start_date).normalize()
    end = pd.Timestamp(args.end_date).normalize()
    if start > end:
        raise ValueError("start-date must not exceed end-date")
    if output_root.exists():
        raise FileExistsError(f"Tier 1 output already exists: {output_root}")

    afml_manifest_path = afml_root / "manifest.json"
    etf_manifest_path = etf_root / "result_manifest.json"
    price_manifest_path = data_store / "manifests" / "daily_price_volume_etf_constituents.json"
    state_manifest_path = data_store / "manifests" / "daily_market_state.json"
    _read_json(afml_manifest_path)
    _read_json(etf_manifest_path)
    _validate_ready_manifest(price_manifest_path, "daily_price_volume_etf_constituents")
    _validate_ready_manifest(state_manifest_path, "daily_market_state")
    if _read_json(price_manifest_path).get("source_price_semantics") != "raw_unadjusted_execution_prices":
        raise ValueError("price snapshot is not raw unadjusted execution pricing")

    bars_path = afml_root / "tables" / "dollar_bars.parquet"
    membership_path = afml_root / "tables" / "bar_daily_membership.parquet"
    nav_path = etf_root / "daily_etf.parquet"
    holdings_path = etf_root / "daily_holdings.parquet"
    for path in (bars_path, membership_path, nav_path, holdings_path):
        if not path.is_file():
            raise ValueError(f"required input is missing: {path}")

    config = Tier1TargetConfig(
        volatility_span=args.volatility_span,
        min_obs=args.min_obs,
        pt_mult=args.pt_mult,
        sl_mult=args.sl_mult,
        vertical_bars=args.vertical_bars,
        buy_cost_rate=args.buy_cost_rate,
        sell_cost_rate=args.sell_cost_rate,
    )
    years = list(range(start.year, end.year + 1))
    prices, states = ExecutionMarketSnapshot.read_bounded_constituent_snapshot(data_store, years)
    bars = pd.read_parquet(bars_path)
    holdings = pd.read_parquet(holdings_path)
    membership = pd.read_parquet(membership_path)
    nav = pd.read_parquet(nav_path)
    member_dates = membership[["etf_id", "date"]].drop_duplicates()
    nav = nav.merge(member_dates, on=["etf_id", "date"], how="inner", validate="one_to_one")
    targets = build_target_table(
        bars,
        holdings,
        prices,
        states,
        nav,
        membership,
        config=config,
        start_date=start,
        end_date=end,
    )
    metadata = build_target_metadata(
        afml_manifest_path=afml_manifest_path,
        etf_manifest_path=etf_manifest_path,
        price_manifest_path=price_manifest_path,
        market_state_manifest_path=state_manifest_path,
        start_date=str(start.date()),
        end_date=str(end.date()),
        config=config,
    )
    metadata.update(
        {
            "afml_root": str(afml_root),
            "etf_result_root": str(etf_root),
            "daily_close_path": str(membership_path),
            "holdings_path": str(holdings_path),
            "input_source_policy": "bounded_constituent_snapshot_only",
        }
    )
    manifest = write_target_artifact(targets, output_root, metadata)
    print(
        {
            "row_count": len(targets),
            "resolved_count": int(targets["target_status"].str.startswith("resolved_").sum()),
            "targets_sha256": manifest["tables"]["targets"]["sha256"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
