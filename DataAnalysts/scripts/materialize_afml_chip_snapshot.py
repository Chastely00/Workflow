"""Materialize the bounded ETF-Trick constituent daily-chip source snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def _project_source_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src"


sys.path.insert(0, str(_project_source_root()))

from data_analysts.afml_chip_snapshot import build_and_publish_afml_chip_snapshot  # noqa: E402
from data_analysts.config import load_runtime_config  # noqa: E402
from data_analysts.extract import open_mongo_databases  # noqa: E402
from data_analysts.paths import DataAnalystsContext  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--data-store")
    parser.add_argument("--holdings-path", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()

    context = DataAnalystsContext.from_paths(args.project_root, args.data_store)
    config = load_runtime_config(context)
    family = next(
        row for row in config.source_family_profiles["families"]
        if row["family_id"] == "daily_chip"
    )
    database = open_mongo_databases(config.mongodb_sources)[family["connection"]]
    result = build_and_publish_afml_chip_snapshot(
        context,
        contract=config.artifact_contracts["daily_chip_etf_constituents"],
        holdings_path=args.holdings_path,
        database=database,
        family=family,
        pit_registry=config.pit_registry,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    print({
        "ticker_count": result["ticker_count"],
        "row_count": result["row_count"],
        "status": result["manifest"]["status"],
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
