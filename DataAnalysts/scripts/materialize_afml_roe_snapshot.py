"""Materialize a bounded, source-provenance-preserving R103 snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_analysts.afml_roe_snapshot import resolve_roe_snapshot_rows
from data_analysts.config import load_runtime_config
from data_analysts.dataset_publication import publish_dataset
from data_analysts.extract import (
    extract_family_rows_from_database,
    open_mongo_databases,
)
from data_analysts.paths import DataAnalystsContext
from data_analysts.raw_families import normalize_raw_family


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a bounded R103 snapshot for actual ETF constituents."
    )
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--data-store")
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2026-07-07")
    parser.add_argument(
        "--holdings-path",
        default=(
            ".artifacts/etf_tricks/full-history-20050103-20260707-v5/"
            "daily_holdings.parquet"
        ),
    )
    args = parser.parse_args()
    if args.start_date > args.end_date:
        raise ValueError("--start-date must be on or before --end-date")

    context = DataAnalystsContext.from_paths(args.project_root, args.data_store)
    config = load_runtime_config(context)
    holdings_path = Path(args.holdings_path)
    if not holdings_path.is_file():
        raise FileNotFoundError(f"missing holdings artifact: {holdings_path}")
    holding_tickers = {
        str(value)
        for value in pq.read_table(holdings_path, columns=["ticker"])["ticker"].to_pylist()
        if value is not None
    }
    if not holding_tickers:
        raise ValueError("holdings artifact has no tickers")
    family = next(
        item
        for item in config.source_family_profiles["families"]
        if item["family_id"] == "financial_statement_raw"
    )
    started = perf_counter()
    raw_rows = extract_family_rows_from_database(
        open_mongo_databases(config.mongodb_sources)["tej"],
        family,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    normalized = normalize_raw_family(
        "financial_statement_raw", raw_rows, config.pit_registry
    )["raw_rows"]
    constituent_rows = [
        row for row in normalized if str(row.get("ticker")) in holding_tickers
    ]
    resolved = resolve_roe_snapshot_rows(constituent_rows)
    result = publish_dataset(
        context,
        config.artifact_contracts["afml_roe_etf_constituents"],
        resolved,
        "bounded_backfill",
    )
    print(
        json.dumps(
            {
                "elapsed_seconds": round(perf_counter() - started, 3),
                "holding_ticker_count": len(holding_tickers),
                "source_row_count": len(raw_rows),
                "constituent_source_row_count": len(constituent_rows),
                "resolved_row_count": len(resolved),
                "r103_conflict_count": sum(
                    bool(row["r103_conflict"]) for row in resolved
                ),
                "manifest_path": str(result.manifest_path),
                "active_version": result.manifest["active_version"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
