"""Bounded, read-only extraction of TEJ APISTKATTR market-state inputs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from time import perf_counter
from typing import Any
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.artifacts import atomic_write_text
from data_analysts.paths import DataAnalystsContext


_PROJECTION = {
    "_id": 0, "coid": 1, "mdate": 1, "mkt": 1, "stktp_e": 1,
    "atten_fg": 1, "disp_fg": 1, "full_fg": 1, "limit_fg": 1,
    "limo_fg": 1, "sbadt_fg": 1, "ssadt_fg": 1, "susp_fg": 1,
}


def extract_bounded_tradability_rows(
    database: Any,
    *,
    start_date: str,
    end_date: str,
    workers: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, float | int]]:
    """Fetch APISKTATTR once per ticker with narrow projections.

    This intentionally performs no per-session queries and no SQLite staging.
    The bounded source window is evaluated in Mongo, then canonical rows are
    sorted once in memory for deterministic parquet publication.
    """
    if workers < 1:
        raise ValueError("workers must be positive")
    names = sorted(
        name for name in database.list_collection_names()
        if not name.startswith("system.")
    )
    started = perf_counter()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tej-attr") as pool:
        batches = list(pool.map(
            lambda name: _extract_ticker(database[name], name, start_date, end_date), names
        ))
    rows = [row for batch in batches for row in batch]
    rows.sort(key=lambda row: (str(row["date"]), str(row["ticker"])))
    keys = [(row["date"], row["ticker"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("APISTKATTR extraction produced duplicate (date, ticker) keys")
    return rows, {
        "collection_count": len(names),
        "row_count": len(rows),
        "mongo_extract_seconds": perf_counter() - started,
    }


def extract_identity_seed_rows(
    database: Any, *, before_date: str, workers: int = 8
) -> list[dict[str, Any]]:
    """Read at most one final APISKTATTR identity row per ticker before a bound."""
    names = sorted(name for name in database.list_collection_names() if not name.startswith("system."))
    bound = datetime.fromisoformat(before_date)
    def one(name: str) -> dict[str, Any] | None:
        source = database[name].find(
            {"$or": [{"mdate": {"$lt": bound}}, {"mdate": {"$lt": before_date}}]}, _PROJECTION
        ).sort("mdate", -1).limit(1)
        values = list(source)
        if not values:
            return None
        row = values[0]
        raw_date = row.get("mdate")
        day = raw_date.date().isoformat() if isinstance(raw_date, datetime) else str(raw_date)[:10]
        return {
            **{key: row.get(key) for key in _PROJECTION if key != "_id"},
            "date": day, "ticker": name, "source_available_date": day,
            "source_collection": name, "source_row_id": f"{name}:{day}",
            "source_dataset_id": "daily_tradability",
        }
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tej-attr-seed") as pool:
        return sorted((row for row in pool.map(one, names) if row is not None), key=lambda row: (row["date"], row["ticker"]))


def publish_bounded_tradability_rows(
    context: DataAnalystsContext,
    rows: list[dict[str, Any]],
    *,
    build_start: str,
    build_end: str,
    data_cutoff_at: str,
) -> dict[str, Any]:
    """Publish a bounded COW APISKTATTR version without SQLite key staging."""
    started = perf_counter()
    if not rows:
        raise ValueError("daily_tradability bounded publication cannot be empty")
    keys = [(str(row["date"]), str(row["ticker"])) for row in rows]
    if keys != sorted(keys) or any(left == right for left, right in zip(keys, keys[1:])):
        raise ValueError("daily_tradability rows must be pre-sorted and unique")
    version = uuid.uuid4().hex
    by_year: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row["data_cutoff_at"] = data_cutoff_at
        by_year.setdefault(str(row["date"])[:4], []).append(row)
    paths: list[str] = []
    inventory: list[dict[str, Any]] = []
    for year, year_rows in sorted(by_year.items()):
        relative = f"canonical/raw/daily_tradability/versions/{version}/year={year}/part.parquet"
        target = context.artifact_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.staging")
        table = pa.Table.from_pylist(year_rows)
        pq.write_table(table, staging, compression="zstd")
        os.replace(staging, target)
        paths.append(relative)
        inventory.append(_inventory_item(relative, target, table.num_rows))
    schema_fingerprints = {item["schema_fingerprint"] for item in inventory}
    if len(schema_fingerprints) != 1:
        raise ValueError("daily_tradability yearly parquet schemas differ")
    manifest = {
        "artifact_id": "daily_tradability", "contract_key": "daily_tradability",
        "variant": "default", "schema_version": "1.0", "layer": "raw",
        "source_families": ["daily_tradability"],
        "source_collections": sorted({str(row["source_collection"]) for row in rows}),
        "row_count": len(rows), "date_range": [build_start, build_end],
        "availability_date_range": [build_start, build_end],
        "columns": list(pa.Table.from_pylist(rows[:1]).column_names),
        "schema_fingerprint": schema_fingerprints.pop(), "partitioning": ["year"],
        "artifact_paths": paths, "partition_inventory": inventory,
        "pit_policy": "source_available_date", "data_cutoff_at": data_cutoff_at,
        "duplicate_count": 0, "omitted_row_count": 0, "status": "ready",
        "created_at": datetime.now().astimezone().isoformat(), "active_version": version,
        "build_start": build_start, "build_end": build_end,
        "publication_policy_version": "bounded_arrow_cow_v1",
    }
    atomic_write_text(
        context.store_path("manifests", "daily_tradability.json"),
        json.dumps(manifest, indent=2, sort_keys=True),
    )
    return {"manifest": manifest, "publish_seconds": perf_counter() - started}


def _inventory_item(relative: str, path: Any, row_count: int) -> dict[str, Any]:
    schema_fingerprint = hashlib.sha256(pq.read_schema(path).serialize().to_pybytes()).hexdigest()
    return {
        "path": relative, "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size, "row_count": row_count,
        "schema_fingerprint": schema_fingerprint,
    }


def _extract_ticker(
    collection: Any, ticker: str, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59)
    query = {"$or": [
        {"mdate": {"$gte": start, "$lte": end}},
        {"mdate": {"$gte": start_date, "$lte": f"{end_date} 23:59:59"}},
    ]}
    result: list[dict[str, Any]] = []
    for source in collection.find(query, _PROJECTION):
        raw_date = source.get("mdate")
        if isinstance(raw_date, datetime):
            day = raw_date.date().isoformat()
        else:
            day = str(raw_date)[:10]
        if not (start_date <= day <= end_date):
            continue
        observed_ticker = str(source.get("coid") or ticker).strip()
        if observed_ticker != ticker:
            raise ValueError(f"APISTKATTR collection identity mismatch: {ticker} != {observed_ticker}")
        result.append({
            **{key: source.get(key) for key in _PROJECTION if key != "_id"},
            "date": day, "ticker": ticker, "source_available_date": day,
            "source_collection": ticker,
            "source_row_id": f"{ticker}:{day}",
            "source_dataset_id": "daily_tradability",
        })
    return result
