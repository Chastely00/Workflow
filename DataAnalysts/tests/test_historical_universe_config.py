import json
from pathlib import Path

import pytest

from data_analysts.config import ConfigError, load_runtime_config
from data_analysts.paths import DataAnalystsContext


ROOT = Path(__file__).resolve().parents[1]


def _copy_configs(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
        "artifact_contracts.json",
    ]:
        (tmp_path / "configs" / name).write_text(
            (ROOT / "configs" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def test_universe_specs_define_baseline_historical_universes():
    payload = json.loads((ROOT / "configs" / "universe_specs.json").read_text(encoding="utf-8"))
    enabled_universe_ids = {
        item["universe_id"]
        for item in payload["universes"]
        if item.get("enabled", True)
    }
    assert enabled_universe_ids == {
        "tw_equity_all_listed",
        "tw_common_stock_all",
        "tw_common_stock_tradable",
        "tw_equity_liquid_top100",
        "tw_equity_liquid_top300",
        "tw_equity_liquid_top500",
        "twse_common_stock",
        "tpex_common_stock",
    }


def test_universe_config_allows_effective_date_but_rejects_realized_return(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config_path = tmp_path / "configs" / "universe_specs.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["universes"][0]["filters"].append({"field": "effective_date", "op": "not_null"})
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    load_runtime_config(context)

    payload["universes"][0]["filters"].append({"field": "realized_return_20d", "op": "not_null"})
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="unsupported field"):
        load_runtime_config(context)


def test_universe_config_rejects_unsupported_operator(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config_path = tmp_path / "configs" / "universe_specs.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["universes"][0]["filters"][0]["op"] = "lte"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="unsupported operator"):
        load_runtime_config(context)
