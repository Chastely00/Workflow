from __future__ import annotations

import hashlib
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import pyarrow.parquet as pq

from data_analysts.config import load_runtime_config
from data_analysts.daily_market_state import build_daily_market_state_rows
from data_analysts.daily_market_state_publication import (
    publish_daily_market_state,
    publish_daily_market_state_partitions,
)
from data_analysts.paths import DataAnalystsContext
from data_analysts.tej_tradability import extract_identity_seed_rows
from pymongo import MongoClient


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize certified daily market state for a bounded date range."
    )
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-07-07")
    parser.add_argument(
        "--chunk-by-year",
        action="store_true",
        help="Build and publish one calendar year at a time to bound peak memory.",
    )
    args = parser.parse_args()
    start, end = args.start, args.end
    if start > end:
        raise ValueError("--start must be on or before --end")
    context = DataAnalystsContext.from_paths("DataAnalysts", "data_store")
    root = context.data_store
    started = perf_counter()
    manifests = {
        name: json.loads((root / "manifests" / f"{name}.json").read_text())
        for name in ("security_master", "trading_calendar", "daily_price_volume", "daily_tradability")
    }
    def rows(name: str, years: set[str] | None = None):
        paths = manifests[name]["artifact_paths"]
        if years:
            paths = [path for path in paths if any(f"year={year}/" in path.replace("\\", "/") for year in years)]
        return [row for path in paths for row in pq.read_table(root / path).to_pylist()]
    calendar, master = rows("trading_calendar"), rows("security_master")
    years = {str(year) for year in range(int(start[:4]), int(end[:4]) + 1)}
    loaded = perf_counter()
    hashes = {name: hashlib.sha256((root / "manifests" / f"{name}.json").read_bytes()).hexdigest() for name in manifests}
    cutoff = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    database = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=5000)["APISTKATTR"]
    if args.chunk_by_year:
        def partition_rows():
            for year in sorted(years):
                scope_start = max(start, f"{year}-01-01")
                scope_end = min(end, f"{year}-12-31")
                prices = rows("daily_price_volume", {year})
                attributes = rows("daily_tradability", {year})
                seeds = extract_identity_seed_rows(database, before_date=scope_start)
                yield year, build_daily_market_state_rows(
                    trading_calendar_rows=calendar,
                    price_rows=prices,
                    security_master_rows=master,
                    attribute_rows=[*seeds, *attributes],
                    manifest_hashes=hashes,
                    build_start=start,
                    build_end=end,
                    data_cutoff_at=cutoff,
                    scope_start=scope_start,
                    scope_end=scope_end,
                )

        result = publish_daily_market_state_partitions(
            context, load_runtime_config(context), partition_rows(), build_start=start,
            build_end=end, certified_source_start=start,
        )
        state_row_count = result.total_row_count
    else:
        prices, attributes = rows("daily_price_volume", years), rows("daily_tradability", years)
        seeds = extract_identity_seed_rows(database, before_date=start)
        state = build_daily_market_state_rows(
            trading_calendar_rows=calendar, price_rows=prices, security_master_rows=master,
            attribute_rows=[*seeds, *attributes], manifest_hashes=hashes, build_start=start,
            build_end=end, data_cutoff_at=cutoff,
        )
        result = publish_daily_market_state(
            context, load_runtime_config(context), state, build_start=start, build_end=end,
            certified_source_start=start,
        )
        state_row_count = len(state)
    classified = perf_counter()
    published = perf_counter()
    print(json.dumps({"input_load_seconds": loaded-started, "classify_and_publish_seconds": classified-loaded, "post_publish_seconds": published-classified, "total_seconds": published-started, "row_count": state_row_count, "active_version": result.manifest["active_version"]}, sort_keys=True))


if __name__ == "__main__":
    main()
