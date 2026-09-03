"""Bounded, read-only extraction of TEJ APISTKATTR market-state inputs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import perf_counter
from typing import Any


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
