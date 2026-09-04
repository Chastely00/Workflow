"""Bounded raw-price source snapshots for ETF Trick AFML execution research."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.artifacts import atomic_write_text
from data_analysts.dataset_publication import publish_dataset
from data_analysts.extract import extract_family_rows_from_database
from data_analysts.paths import DataAnalystsContext


_PRICE_SNAPSHOT_COLUMNS = (
    "date", "ticker", "open", "high", "low", "close", "volume", "traded_value",
    "source_available_date", "data_cutoff_at", "data_cutoff_origin", "source_collection",
    "source_row_id",
)
_PRICE_SNAPSHOT_SCHEMA = pa.schema([
    pa.field("date", pa.string()), pa.field("ticker", pa.string()),
    pa.field("open", pa.float64()), pa.field("high", pa.float64()),
    pa.field("low", pa.float64()), pa.field("close", pa.float64()),
    pa.field("volume", pa.float64()), pa.field("traded_value", pa.float64()),
    pa.field("source_available_date", pa.string()), pa.field("data_cutoff_at", pa.string()),
    pa.field("data_cutoff_origin", pa.string()), pa.field("source_collection", pa.string()),
    pa.field("source_row_id", pa.string()),
])
_RAW_FIELD_MAP = {
    "open": "open_d", "high": "high_d", "low": "low_d", "close": "close_d",
    "volume": "vol", "traded_value": "amt",
}


def build_and_publish_afml_price_snapshot(
    context: DataAnalystsContext,
    *,
    contract: ArtifactContract,
    holdings_path: str | Path,
    database: Any,
    family: dict[str, Any],
    pit_registry: dict[str, Any],
    start_date: str,
    end_date: str,
    extraction_completed_at: str | None = None,
) -> dict[str, Any]:
    """Publish raw, unadjusted price rows for historically held constituents only."""
    del pit_registry
    if contract.artifact_id != "daily_price_volume_etf_constituents":
        raise ValueError("AFML price snapshot requires daily_price_volume_etf_constituents contract")
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")
    holdings_file = Path(holdings_path)
    tickers = _constituent_tickers(holdings_file, start_date=start_date, end_date=end_date)
    if not tickers:
        raise ValueError("AFML price snapshot holdings universe is empty")
    source_family = dict(family)
    source_family["tickers"] = tickers
    extracted = extract_family_rows_from_database(
        database, source_family, start_date=start_date, end_date=end_date,
        run_scope="bounded_backfill", extraction_completed_at=extraction_completed_at,
    )
    rows = _canonical_price_rows(extracted, start_date=start_date, end_date=end_date)
    if not rows:
        raise ValueError("AFML price snapshot extraction returned no in-window rows")
    result = publish_dataset(
        context, contract, rows, "bounded_backfill", write_schema=_PRICE_SNAPSHOT_SCHEMA,
    )
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({
        "constituent_universe": {
            "holdings_path_sha256": _sha256_file(holdings_file),
            "holdings_window": [start_date, end_date], "ticker_count": len(tickers), "tickers": tickers,
        },
        "revision_status": "PIT_REVISION_UNVERIFIED",
        "revision_reason": "MongoDB extraction cutoff is observed at extraction time; historical source vintages are not available.",
        "snapshot_kind": "afml_etf_constituent_daily_price_volume",
        "snapshot_columns": list(_PRICE_SNAPSHOT_COLUMNS),
        "source_price_semantics": "raw_unadjusted_execution_prices",
        "constituent_selection_policy": (
            "The bounded ticker set is source coverage only. Downstream execution must join each "
            "historical price row to holdings valid before that execution date."
        ),
    })
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
    return {"manifest": manifest, "ticker_count": len(tickers), "row_count": len(rows)}


def _constituent_tickers(holdings_path: Path, *, start_date: str, end_date: str) -> list[str]:
    table = pq.read_table(holdings_path, columns=["date", "ticker"])
    tickers: set[str] = set()
    for row in table.to_pylist():
        value, ticker = _date_text(row.get("date")), str(row.get("ticker") or "").strip()
        if value is None or not ticker:
            raise ValueError("holdings requires non-empty date and ticker")
        if start_date <= value <= end_date:
            tickers.add(ticker)
    return sorted(tickers)


def _canonical_price_rows(extracted: list[dict[str, Any]], *, start_date: str, end_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in extracted:
        value, ticker = _date_text(source.get("mdate")), str(source.get("coid") or source.get("ticker") or "").strip()
        if value is None or not ticker:
            raise ValueError("daily_price_volume source row requires coid and mdate")
        if not start_date <= value <= end_date:
            continue
        cutoff = _cutoff_text(source.get("data_cutoff_at"))
        if cutoff is None:
            raise ValueError("daily_price_volume source row requires data_cutoff_at")
        rows.append({
            "date": value, "ticker": ticker,
            **{name: _optional_float(source.get(field)) for name, field in _RAW_FIELD_MAP.items()},
            "source_available_date": value, "data_cutoff_at": cutoff,
            "data_cutoff_origin": str(source.get("data_cutoff_origin") or "source_reported"),
            "source_collection": str(source.get("source_collection") or ticker),
            "source_row_id": str(source.get("source_row_id") or ""),
        })
    rows.sort(key=lambda row: (str(row["date"]), str(row["ticker"])))
    keys = [(row["date"], row["ticker"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("AFML price snapshot has duplicate (date, ticker) keys")
    return rows


def _date_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value[:10] if len(value) >= 10 else None
    return None


def _cutoff_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00Z"
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_float(value: Any) -> float:
    if value is None or value == "":
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"daily_price_volume numeric value is invalid: {value!r}") from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
