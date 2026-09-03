import json
from pathlib import Path

import pytest

from data_analysts.config import ConfigError, load_runtime_config
from data_analysts.paths import DataAnalystsContext
from data_analysts.source_catalog import load_pit_registry, load_source_catalog


ROOT = Path(__file__).resolve().parents[1]


def load_json(relative_path):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_source_catalog_declares_forbidden_sources():
    catalog = load_json("configs/source_catalog.json")
    forbidden = {(item["database"], item["collection"]) for item in catalog["forbidden_sources"]}
    assert ("TEJ", "AINVFQ1") in forbidden
    assert ("TEJ", "APISHRACTW") in forbidden
    assert len(forbidden) == 2


def test_pit_registry_declares_ainvfinb_revision_rule():
    registry = load_json("configs/pit_registry.json")
    rule = registry["families"]["financial_statement_raw"]
    assert rule["database"] == "TEJ"
    assert rule["collection"] == "AINVFINB"
    assert rule["availability_field"] == "key3"
    assert rule["revision_field"] == "mdate"
    assert rule["date_normalization"] == "date_only"
    assert rule["preserve_revisions"] is True


def test_pit_registry_declares_ainvfinb_same_day_key3_timestamp_tie_breaker():
    registry = load_json("configs/pit_registry.json")
    rule = registry["families"]["financial_statement_pit_selected"]
    assert rule["availability_field"] == "source_available_date"
    assert rule["source_timestamp_tie_breaker"] == {
        "field": "key3",
        "scope": "same_normalized_source_available_date",
        "order": "max_timestamp",
    }


def test_load_runtime_config_includes_source_catalog_and_pit_registry():
    config = load_runtime_config(DataAnalystsContext.from_paths(ROOT))
    assert "sources" in config.source_catalog
    assert "families" in config.pit_registry


def test_source_catalog_loaders_validate_static_configs():
    context = DataAnalystsContext.from_paths(ROOT)
    catalog = load_source_catalog(context)
    registry = load_pit_registry(context)
    assert catalog["sources"]
    assert registry["families"]["financial_statement_raw"]["collection"] == "AINVFINB"


def test_load_runtime_config_rejects_registry_missing_catalog_family(tmp_path):
    root = tmp_path
    (root / "configs").mkdir()
    for filename in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
            "pit_registry.json",
            "artifact_contracts.json",
        ]:
        (root / "configs" / filename).write_text(
            (ROOT / "configs" / filename).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    registry = json.loads((root / "configs" / "pit_registry.json").read_text(encoding="utf-8"))
    registry["families"].pop("daily_chip")
    (root / "configs" / "pit_registry.json").write_text(
        json.dumps(registry),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="catalog/registry family mismatch"):
        load_runtime_config(DataAnalystsContext.from_paths(root))
