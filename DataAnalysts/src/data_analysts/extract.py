from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
from typing import Any, Protocol

from data_analysts.artifact_contracts import RunScope


DEFAULT_LOCALHOST_MONGODB_URI = "mongodb://localhost:27017/"


class ExtractError(ValueError):
    """Raised when a source cannot be extracted safely."""


class CollectionLike(Protocol):
    def find(self, query: dict[str, Any] | None = None): ...


class DatabaseLike(Protocol):
    def __getitem__(self, name: str) -> CollectionLike: ...

    def list_collection_names(self): ...


def extract_rows_from_collection(
    collection: CollectionLike,
    family: dict[str, Any],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    run_scope: RunScope = "bounded_backfill",
) -> list[dict[str, Any]]:
    source_profile = family.get("source_profile")
    if source_profile == "small_snapshot":
        return [_clean_mongo_row(row) for row in collection.find({})]
    if source_profile == "medium_pit_table":
        return [_clean_mongo_row(row) for row in collection.find(_medium_pit_query(family, start_date, end_date))]
    if source_profile == "large_daily_panel":
        if not start_date or not end_date:
            if run_scope == "full_history":
                return [_clean_mongo_row(row) for row in collection.find({})]
            raise ExtractError(f"{family.get('family_id')} requires start-date and end-date")
        return [_clean_mongo_row(row) for row in collection.find(_large_daily_query(family, start_date, end_date))]
    raise ExtractError(f"unsupported source_profile: {source_profile}")


def extract_family_rows_from_database(
    database: DatabaseLike,
    family: dict[str, Any],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    run_scope: RunScope = "bounded_backfill",
    extraction_completed_at: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for collection_name in resolve_collection_names(database, family):
        collection_rows = extract_rows_from_collection(
            database[collection_name],
            family,
            start_date=start_date,
            end_date=end_date,
            run_scope=run_scope,
        )
        collection_rows.reverse()
        while collection_rows:
            row = collection_rows.pop()
            row.setdefault("source_collection", collection_name)
            row.setdefault(
                "source_row_id",
                _stable_source_row_id(collection_name, row, family),
            )
            rows.append(row)
    if family.get("data_cutoff_policy") == "extraction_completed_fallback":
        cutoff = _extraction_completed_at(extraction_completed_at)
        for row in rows:
            if not _is_real_cutoff(row.get("data_cutoff_at")):
                row["data_cutoff_at"] = cutoff
                row["data_cutoff_origin"] = "extraction_completed_fallback"
    return rows


def _extraction_completed_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
    if not _is_real_cutoff(value):
        raise ExtractError(f"invalid extraction_completed_at: {value!r}")
    return value


def _is_real_cutoff(value: Any) -> bool:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and ("T" in value or " " in value):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp() != 0.0


def resolve_collection_names(database: DatabaseLike, family: dict[str, Any]) -> list[str]:
    collection = family.get("collection")
    if collection:
        return [str(collection)]

    pattern = family.get("collection_pattern")
    if not pattern:
        raise ExtractError(f"{family.get('family_id')} must define collection or collection_pattern")

    tickers = family.get("tickers")
    if isinstance(tickers, list) and tickers:
        return [str(pattern).replace("{ticker}", str(ticker)) for ticker in tickers]

    if "{ticker}" not in str(pattern):
        return [str(pattern)]

    regex = "^" + re.escape(str(pattern)).replace(re.escape("{ticker}"), r"[^.]+") + "$"
    matches = [name for name in database.list_collection_names() if re.match(regex, name)]
    if not matches:
        raise ExtractError(f"no collections match pattern for {family.get('family_id')}: {pattern}")
    return sorted(matches)


def open_mongo_databases(config: dict[str, Any]) -> dict[str, Any]:
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise ExtractError("pymongo is required for MongoDB extraction") from exc

    databases: dict[str, Any] = {}
    for connection_id, connection in config.get("connections", {}).items():
        uri = resolve_mongodb_uri(connection)
        database_name = connection.get("database")
        if not database_name:
            raise ExtractError(f"connection {connection_id} must define database")
        databases[connection_id] = MongoClient(uri, serverSelectionTimeoutMS=5000)[database_name]
    return databases


def resolve_mongodb_uri(connection: dict[str, Any], environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    uri_env = connection.get("uri_env")
    if uri_env and env.get(uri_env):
        return str(env[uri_env])
    return str(connection.get("default_uri") or DEFAULT_LOCALHOST_MONGODB_URI)


def _medium_pit_query(
    family: dict[str, Any],
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    availability = family.get("availability") or {}
    fields = [availability.get("field"), *family.get("event_date_fields", [])]
    fields = [str(field) for field in fields if field]
    if not fields or not start_date or not end_date:
        return {}
    clauses: list[dict[str, Any]] = []
    for field in dict.fromkeys(fields):
        clauses.extend(_date_window_query_clauses(field, start_date, end_date))
    return {"$or": clauses}


def _large_daily_query(family: dict[str, Any], start_date: str, end_date: str) -> dict[str, Any]:
    date_fields = family.get("date_fields") or {}
    field = date_fields.get("source_date")
    if not field:
        raise ExtractError(f"{family.get('family_id')} has no source_date field")
    return _date_window_query(field, start_date, end_date)


def _inclusive_day_upper_bound(value: str) -> str:
    if len(value) == 10:
        return f"{value} 23:59:59"
    return value


def _date_window_query(field: str, start_date: str, end_date: str) -> dict[str, Any]:
    return {"$or": _date_window_query_clauses(field, start_date, end_date)}


def _date_window_query_clauses(field: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return [
        {field: {"$gte": start_date, "$lte": _inclusive_day_upper_bound(end_date)}},
        {field: {"$gte": _date_start(start_date), "$lte": _date_end(end_date)}},
    ]


def _date_start(value: str) -> datetime:
    return datetime.fromisoformat(value[:10])


def _date_end(value: str) -> datetime:
    return datetime.fromisoformat(value[:10]).replace(hour=23, minute=59, second=59)


def _clean_mongo_row(row: dict[str, Any]) -> dict[str, Any]:
    cleaned = {str(key): value for key, value in dict(row).items() if str(key) != "_id"}
    if row.get("_id") is not None:
        cleaned["__mongo_source_id"] = str(row["_id"])
    return cleaned


def _stable_source_row_id(
    collection_name: str,
    row: dict[str, Any],
    family: dict[str, Any],
) -> str:
    mongo_id = row.pop("__mongo_source_id", None)
    configured_keys = (
        family.get("source_primary_keys")
        or family.get("primary_keys")
        or family.get("primary_key")
    )
    field_map = family.get("field_map") if isinstance(family.get("field_map"), dict) else {}
    canonical_key_values: dict[str, Any] = {}
    if isinstance(configured_keys, list) and configured_keys:
        for key in configured_keys:
            source_key = field_map.get(key, key)
            if source_key in row and row[source_key] is not None:
                canonical_key_values[str(key)] = _canonical_identity_value(row[source_key])
    if (
        isinstance(configured_keys, list)
        and configured_keys
        and len(canonical_key_values) == len(configured_keys)
    ):
        identity = canonical_key_values
        identity_kind = "pk-sha256"
    elif mongo_id is not None:
        return f"{collection_name}:{mongo_id}"
    else:
        identity = {
            str(key): _canonical_identity_value(value)
            for key, value in row.items()
            if key not in {"source_row_id", "source_collection"}
        }
        identity_kind = "sha256"
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"{collection_name}:{identity_kind}:{digest}"


def _canonical_identity_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical_identity_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_identity_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
