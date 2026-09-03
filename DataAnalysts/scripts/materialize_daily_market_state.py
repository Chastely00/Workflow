from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import pyarrow.parquet as pq

from data_analysts.config import load_runtime_config
from data_analysts.daily_market_state import build_daily_market_state_rows
from data_analysts.daily_market_state_publication import publish_daily_market_state
from data_analysts.paths import DataAnalystsContext


def main() -> None:
    start, end = "2024-01-01", "2026-07-07"
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
    prices, attributes = rows("daily_price_volume", {"2024", "2025", "2026"}), rows("daily_tradability", {"2024", "2025", "2026"})
    loaded = perf_counter()
    hashes = {name: hashlib.sha256((root / "manifests" / f"{name}.json").read_bytes()).hexdigest() for name in manifests}
    cutoff = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state = build_daily_market_state_rows(
        trading_calendar_rows=calendar, price_rows=prices, security_master_rows=master,
        attribute_rows=attributes, manifest_hashes=hashes, build_start=start,
        build_end=end, data_cutoff_at=cutoff,
    )
    classified = perf_counter()
    result = publish_daily_market_state(
        context, load_runtime_config(context), state, build_start=start, build_end=end,
        certified_source_start=start,
    )
    published = perf_counter()
    print(json.dumps({"input_load_seconds": loaded-started, "classify_seconds": classified-loaded, "publish_seconds": published-classified, "total_seconds": published-started, "row_count": len(state), "active_version": result.manifest["active_version"]}, sort_keys=True))


if __name__ == "__main__":
    main()
