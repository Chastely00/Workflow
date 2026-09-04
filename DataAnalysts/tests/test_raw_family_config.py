import json
from pathlib import Path

import pytest

from data_analysts.config import ConfigError, _validate_families, load_runtime_config
from data_analysts.paths import DataAnalystsContext


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    return json.loads((ROOT / "configs" / name).read_text(encoding="utf-8"))


def test_raw_family_connections_are_declared():
    payload = _load("mongodb_sources.json")
    connections = payload["connections"]
    assert connections["apistkattr"]["database"] == "APISTKATTR"
    assert connections["apishract"]["database"] == "APISHRACT"
    assert connections["futures_taifex_tx"]["database"] == "Futures_TAIFEX_TX"
    assert connections["apistkattr"]["default_uri"] == "mongodb://localhost:27017/"
    assert connections["apishract"]["default_uri"] == "mongodb://localhost:27017/"
    assert connections["futures_taifex_tx"]["default_uri"] == "mongodb://localhost:27017/"


def test_raw_family_profiles_cover_registry_families():
    registry = _load("pit_registry.json")["families"]
    profiles = _load("source_family_profiles.json")["families"]
    profile_ids = {item["family_id"] for item in profiles}
    required = {family_id for family_id, rule in registry.items() if rule["database"] != "derived"}
    assert required <= profile_ids
    assert "financial_statement_pit_selected" not in profile_ids
    assert "self_reported_numbers_pit_selected" not in profile_ids


def test_raw_family_profiles_do_not_use_forbidden_sources():
    profiles = _load("source_family_profiles.json")["families"]
    forbidden = {("tej", "AINVFQ1"), ("tej", "APISHRACTW")}
    used = {
        (str(item.get("connection")), str(item.get("collection")))
        for item in profiles
        if item.get("collection")
    }
    assert forbidden.isdisjoint(used)


def test_self_reported_numbers_raw_primary_key_matches_pit_logical_key():
    profiles = {
        item["family_id"]: item
        for item in _load("source_family_profiles.json")["families"]
    }
    registry = _load("pit_registry.json")["families"]
    catalog = {
        item["family_id"]: item
        for item in _load("source_catalog.json")["sources"]
    }

    family_id = "self_reported_numbers_raw"
    profile_key = profiles[family_id]["primary_key"]
    assert profile_key == registry[family_id]["logical_key"]
    assert profile_key == catalog[family_id]["logical_key"]


def test_runtime_config_loads_with_raw_family_profiles():
    config = load_runtime_config(DataAnalystsContext.from_paths(ROOT))
    assert "trading_calendar" in config.family_ids
    assert "daily_tradability" in config.family_ids
    assert "financial_statement_raw" in config.family_ids
    assert "taiwan_index_futures_near_month" in config.family_ids


def test_all_enabled_mongo_families_declare_extraction_cutoff_recovery():
    profiles = _load("source_family_profiles.json")["families"]
    enabled = [item for item in profiles if item.get("enabled", True)]
    assert all(
        item.get("data_cutoff_policy") == "extraction_completed_fallback"
        for item in enabled
    )


def test_unknown_data_cutoff_policy_is_rejected():
    with pytest.raises(ConfigError, match="unsupported data_cutoff_policy"):
        _validate_families(
            {"families": [{
                "family_id": "daily_chip",
                "enabled": True,
                "connection": "local",
                "source_profile": "large_daily_panel",
                "primary_key": ["date", "ticker"],
                "data_cutoff_policy": "silently_accept_epoch",
            }]},
            {"local": {}},
        )
