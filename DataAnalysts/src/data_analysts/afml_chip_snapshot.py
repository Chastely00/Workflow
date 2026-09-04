"""Bounded daily-chip source snapshots for ETF Trick AFML research."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.artifacts import atomic_write_text
from data_analysts.dataset_publication import publish_dataset
from data_analysts.extract import extract_family_rows_from_database
from data_analysts.paths import DataAnalystsContext


def build_and_publish_afml_chip_snapshot(
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
    """Publish the exact held-constituent chip panel for a bounded AFML window."""
    del pit_registry  # The daily-chip contract has no revision-selection branch.
    if contract.artifact_id != "daily_chip_etf_constituents":
        raise ValueError("AFML chip snapshot requires daily_chip_etf_constituents contract")
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    holdings_file = Path(holdings_path)
    tickers = _constituent_tickers(holdings_file, start_date=start_date, end_date=end_date)
    if not tickers:
        raise ValueError("AFML chip snapshot holdings universe is empty")
    source_family = dict(family)
    source_family["tickers"] = tickers
    extracted = extract_family_rows_from_database(
        database,
        source_family,
        start_date=start_date,
        end_date=end_date,
        run_scope="bounded_backfill",
        extraction_completed_at=extraction_completed_at,
    )
    rows = _canonical_daily_chip_rows(extracted, start_date=start_date, end_date=end_date)
    if not rows:
        raise ValueError("AFML chip snapshot extraction returned no in-window rows")
    result = publish_dataset(context, contract, rows, "bounded_backfill")
    manifest_path = context.store_path("manifests", contract.manifest_file_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "constituent_universe": {
                "holdings_path_sha256": _sha256_file(holdings_file),
                "holdings_window": [start_date, end_date],
                "ticker_count": len(tickers),
                "tickers": tickers,
            },
            "revision_status": "PIT_REVISION_UNVERIFIED",
            "revision_reason": (
                "MongoDB extraction cutoff is observed at extraction time; "
                "historical source vintages are not available."
            ),
            "snapshot_kind": "afml_etf_constituent_daily_chip",
        }
    )
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
    return {"manifest": manifest, "ticker_count": len(tickers), "row_count": len(rows)}


def _constituent_tickers(
    holdings_path: Path, *, start_date: str, end_date: str
) -> list[str]:
    table = pq.read_table(holdings_path, columns=["date", "ticker"])
    tickers: set[str] = set()
    for row in table.to_pylist():
        value = _date_text(row.get("date"))
        ticker = str(row.get("ticker") or "").strip()
        if value is None or not ticker:
            raise ValueError("holdings requires non-empty date and ticker")
        if start_date <= value <= end_date:
            tickers.add(ticker)
    return sorted(tickers)


def _canonical_daily_chip_rows(
    extracted: list[dict[str, Any]], *, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in extracted:
        value = _date_text(source.get("mdate"))
        ticker = str(source.get("coid") or source.get("ticker") or "").strip()
        if value is None or not ticker:
            raise ValueError("daily_chip source row requires coid and mdate")
        if not start_date <= value <= end_date:
            continue
        rows.append({
            **source,
            "date": value,
            "ticker": ticker,
            "source_available_date": value,
        })
    rows.sort(key=lambda row: (str(row["date"]), str(row["ticker"])))
    keys = [(row["date"], row["ticker"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("AFML chip snapshot has duplicate (date, ticker) keys")
    return rows


def _date_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value[:10] if len(value) >= 10 else None
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
