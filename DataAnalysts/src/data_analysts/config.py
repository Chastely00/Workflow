from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from data_analysts.artifact_contracts import (
    ArtifactContract,
    ArtifactContractError,
    parse_artifact_contracts,
)
from data_analysts.paths import DataAnalystsContext
from data_analysts.source_catalog import (
    SourceCatalogError,
    validate_pit_registry,
    validate_source_catalog,
)


SUPPORTED_SCHEMA_VERSION = "1.0"
SUPPORTED_SOURCE_PROFILES = {"small_snapshot", "medium_pit_table", "large_daily_panel"}
SUPPORTED_DATA_CUTOFF_POLICIES = {"source_required", "extraction_completed_fallback"}
SECURITY_PANEL_FIELDS = {
    "as_of_date",
    "effective_date",
    "source_max_date",
    "ticker",
    "stock_name",
    "market",
    "security_type",
    "listed",
    "tradable",
    "close",
    "adj_close",
    "traded_value",
    "market_cap",
    "adv20",
    "data_cutoff_at",
}
SUPPORTED_UNIVERSE_OPERATORS = {"eq", "gte", "not_null"}
CONFIG_FILENAMES = (
    "mongodb_sources.json",
    "source_family_profiles.json",
    "universe_specs.json",
    "source_catalog.json",
    "pit_registry.json",
    "artifact_contracts.json",
)


class ConfigError(ValueError):
    """Raised when DataAnalysts config cannot be safely used."""


@dataclass(frozen=True)
class RuntimeConfig:
    mongodb_sources: dict[str, Any]
    source_family_profiles: dict[str, Any]
    universe_specs: dict[str, Any]
    source_catalog: dict[str, Any]
    pit_registry: dict[str, Any]
    artifact_contracts: dict[str, ArtifactContract]
    family_ids: set[str]
    universe_ids: set[str]


def load_runtime_config(context: DataAnalystsContext) -> RuntimeConfig:
    return load_runtime_config_from_directory(context.project_root / "configs")


def load_runtime_config_from_directory(config_dir: Path) -> RuntimeConfig:
    mongodb_sources = _load_required_json(config_dir / "mongodb_sources.json")
    source_family_profiles = _load_required_json(config_dir / "source_family_profiles.json")
    universe_specs = _load_required_json(config_dir / "universe_specs.json")
    artifact_contract_payload = _load_required_json(config_dir / "artifact_contracts.json")

    _require_schema(mongodb_sources, "mongodb_sources.json")
    _require_schema(source_family_profiles, "source_family_profiles.json")
    _require_schema(universe_specs, "universe_specs.json")
    _reject_plaintext_mongodb_uri(mongodb_sources)
    try:
        source_catalog = validate_source_catalog(
            _load_required_json(config_dir / "source_catalog.json")
        )
        pit_registry = validate_pit_registry(
            _load_required_json(config_dir / "pit_registry.json"),
            source_catalog,
        )
    except SourceCatalogError as exc:
        raise ConfigError(str(exc)) from exc

    connections = mongodb_sources.get("connections")
    if not isinstance(connections, dict):
        raise ConfigError("mongodb_sources.json must define connections")
    family_ids = _validate_families(source_family_profiles, connections)
    universe_ids = _validate_universes(universe_specs)
    try:
        artifact_contracts = parse_artifact_contracts(
            artifact_contract_payload,
            universe_specs,
            source_family_profiles=source_family_profiles,
        )
    except ArtifactContractError as exc:
        raise ConfigError(str(exc)) from exc

    return RuntimeConfig(
        mongodb_sources=mongodb_sources,
        source_family_profiles=source_family_profiles,
        universe_specs=universe_specs,
        source_catalog=source_catalog,
        pit_registry=pit_registry,
        artifact_contracts=artifact_contracts,
        family_ids=family_ids,
        universe_ids=universe_ids,
    )


def _load_required_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config: {path.name}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ConfigError(f"config must be a JSON object: {path.name}")
    return payload


def _require_schema(payload: dict[str, Any], filename: str) -> None:
    if payload.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise ConfigError(f"unsupported schema_version in {filename}")


def _reject_plaintext_mongodb_uri(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "uri":
                raise ConfigError("plaintext MongoDB URI is not allowed")
            if key == "default_uri":
                _validate_localhost_default_uri(value)
            else:
                _reject_plaintext_mongodb_uri(value)
    elif isinstance(payload, list):
        for value in payload:
            _reject_plaintext_mongodb_uri(value)
    elif isinstance(payload, str) and payload.startswith(("mongodb://", "mongodb+srv://")):
        raise ConfigError("plaintext MongoDB URI is not allowed")


def _validate_localhost_default_uri(value: Any) -> None:
    if not isinstance(value, str):
        raise ConfigError("default_uri must be a string")
    parsed = urlparse(value)
    if parsed.scheme not in {"mongodb", "mongodb+srv"}:
        raise ConfigError("default_uri must be a MongoDB URI")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigError("default_uri must be localhost")
    if parsed.username or parsed.password:
        raise ConfigError("default_uri must not contain credentials")


def _validate_families(payload: dict[str, Any], connections: dict[str, Any]) -> set[str]:
    families = payload.get("families")
    if not isinstance(families, list):
        raise ConfigError("source_family_profiles.json must define families")

    seen: set[str] = set()
    for family in families:
        if not isinstance(family, dict):
            raise ConfigError("family config must be a JSON object")
        family_id = family.get("family_id")
        if not isinstance(family_id, str) or not family_id:
            raise ConfigError("family_id is required")
        if family_id in seen:
            raise ConfigError(f"duplicate family_id: {family_id}")
        seen.add(family_id)

        if family.get("enabled", True) is False:
            continue

        source_profile = family.get("source_profile")
        if source_profile not in SUPPORTED_SOURCE_PROFILES:
            raise ConfigError(f"unsupported source_profile for {family_id}: {source_profile}")

        data_cutoff_policy = family.get("data_cutoff_policy", "source_required")
        if data_cutoff_policy not in SUPPORTED_DATA_CUTOFF_POLICIES:
            raise ConfigError(
                f"unsupported data_cutoff_policy for {family_id}: {data_cutoff_policy}"
            )

        connection = family.get("connection")
        if connection not in connections:
            raise ConfigError(f"unknown connection for {family_id}: {connection}")

        primary_key = family.get("primary_key")
        if not isinstance(primary_key, list) or not primary_key:
            raise ConfigError(f"primary_key is required for {family_id}")

    return seen


def _validate_universes(payload: dict[str, Any]) -> set[str]:
    universes = payload.get("universes")
    if not isinstance(universes, list):
        raise ConfigError("universe_specs.json must define universes")

    seen: set[str] = set()
    for universe in universes:
        if not isinstance(universe, dict):
            raise ConfigError("universe config must be a JSON object")
        universe_id = universe.get("universe_id")
        if not isinstance(universe_id, str) or not universe_id:
            raise ConfigError("universe_id is required")
        if universe_id in seen:
            raise ConfigError(f"duplicate universe_id: {universe_id}")
        seen.add(universe_id)

        if universe.get("enabled", True) is False:
            continue
        if universe.get("source") != "security_panel":
            raise ConfigError(f"universe {universe_id} must use security_panel source")
        _validate_universe_fields(universe_id, universe)

    return seen


def _validate_universe_fields(universe_id: str, universe: dict[str, Any]) -> None:
    for filter_rule in universe.get("filters", []):
        field = filter_rule.get("field") if isinstance(filter_rule, dict) else None
        if field not in SECURITY_PANEL_FIELDS:
            raise ConfigError(f"universe {universe_id} uses unsupported field: {field}")
        op = filter_rule.get("op") if isinstance(filter_rule, dict) else None
        if op not in SUPPORTED_UNIVERSE_OPERATORS:
            raise ConfigError(f"universe {universe_id} uses unsupported operator: {op}")
    for rank_rule in universe.get("rank_by", []):
        field = rank_rule.get("field") if isinstance(rank_rule, dict) else None
        if field not in SECURITY_PANEL_FIELDS:
            raise ConfigError(f"universe {universe_id} uses unsupported field: {field}")
