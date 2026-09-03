from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_analysts.paths import DataAnalystsContext


class SourceCatalogError(ValueError):
    """Raised when source catalog or PIT registry config is unsafe."""


def load_source_catalog(context: DataAnalystsContext) -> dict[str, Any]:
    return validate_source_catalog(_load_json(context.config_path("source_catalog.json")))


def load_pit_registry(context: DataAnalystsContext) -> dict[str, Any]:
    catalog = load_source_catalog(context)
    return validate_pit_registry(_load_json(context.config_path("pit_registry.json")), catalog)


def validate_source_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != "1.0":
        raise SourceCatalogError("unsupported schema_version in source_catalog.json")

    sources = payload.get("sources")
    forbidden = payload.get("forbidden_sources")
    if not isinstance(sources, list) or not sources:
        raise SourceCatalogError("source_catalog.json must define sources")
    if not isinstance(forbidden, list) or not forbidden:
        raise SourceCatalogError("source_catalog.json must define forbidden_sources")

    seen: set[str] = set()
    forbidden_pairs = _forbidden_pairs(payload)
    for source in sources:
        if not isinstance(source, dict):
            raise SourceCatalogError("source catalog entries must be objects")
        family_id = str(source.get("family_id") or "")
        if not family_id:
            raise SourceCatalogError("source catalog entry missing family_id")
        if family_id in seen:
            raise SourceCatalogError(f"duplicate source family_id: {family_id}")
        seen.add(family_id)

        _reject_forbidden_source(source, forbidden_pairs)
        if not source.get("pit_field"):
            raise SourceCatalogError(f"source catalog entry missing pit_field: {family_id}")
        _require_date_only(source, "source catalog entry", family_id)
        _require_logical_key(source, "source catalog entry", family_id)

    return payload


def validate_pit_registry(
    payload: dict[str, Any],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    if payload.get("schema_version") != "1.0":
        raise SourceCatalogError("unsupported schema_version in pit_registry.json")

    families = payload.get("families")
    if not isinstance(families, dict) or not families:
        raise SourceCatalogError("pit_registry.json must define families")

    catalog_sources = catalog.get("sources", [])
    if not isinstance(catalog_sources, list):
        raise SourceCatalogError("source_catalog.json must define sources")
    catalog_ids = {
        str(item.get("family_id"))
        for item in catalog_sources
        if isinstance(item, dict) and item.get("family_id")
    }
    registry_ids = {str(family_id) for family_id in families}
    if registry_ids != catalog_ids:
        missing_from_registry = sorted(catalog_ids - registry_ids)
        missing_from_catalog = sorted(registry_ids - catalog_ids)
        details = []
        if missing_from_registry:
            details.append(f"missing_from_registry={','.join(missing_from_registry)}")
        if missing_from_catalog:
            details.append(f"missing_from_catalog={','.join(missing_from_catalog)}")
        raise SourceCatalogError(f"catalog/registry family mismatch: {'; '.join(details)}")
    forbidden_pairs = _forbidden_pairs(catalog)

    for family_id, rule in families.items():
        if family_id not in catalog_ids:
            raise SourceCatalogError(f"pit registry family not in source catalog: {family_id}")
        if not isinstance(rule, dict):
            raise SourceCatalogError(f"pit registry rule must be object: {family_id}")
        _reject_forbidden_source(rule, forbidden_pairs)
        if not rule.get("availability_field"):
            raise SourceCatalogError(f"pit registry missing availability_field: {family_id}")
        _require_date_only(rule, "pit registry", family_id)
        _require_logical_key(rule, "pit registry", family_id)

    return payload


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SourceCatalogError(f"missing config: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceCatalogError(f"invalid JSON in {path.name}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise SourceCatalogError(f"config must be a JSON object: {path.name}")
    return payload


def _forbidden_pairs(catalog: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    forbidden = catalog.get("forbidden_sources", [])
    if not isinstance(forbidden, list):
        return pairs
    for item in forbidden:
        if isinstance(item, dict):
            database = str(item.get("database") or "")
            collection = str(item.get("collection") or "")
            if database and collection:
                pairs.add((database, collection))
    return pairs


def _reject_forbidden_source(
    source: dict[str, Any],
    forbidden_pairs: set[tuple[str, str]],
) -> None:
    database = str(source.get("database") or "")
    collection = str(source.get("collection") or "")
    if (database, collection) in forbidden_pairs:
        raise SourceCatalogError(f"forbidden source referenced: {database}.{collection}")


def _require_date_only(source: dict[str, Any], context: str, family_id: str) -> None:
    if source.get("date_normalization") != "date_only":
        raise SourceCatalogError(f"{context} must use date_normalization=date_only: {family_id}")


def _require_logical_key(source: dict[str, Any], context: str, family_id: str) -> None:
    logical_key = source.get("logical_key")
    if not isinstance(logical_key, list) or not logical_key:
        raise SourceCatalogError(f"{context} missing logical_key: {family_id}")
    if any(not isinstance(column, str) or not column.strip() for column in logical_key):
        raise SourceCatalogError(f"{context} logical_key must be non-empty strings: {family_id}")
