import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from data_analysts.artifact_contracts import (
    ArtifactContractError,
    expected_contract_outputs,
    load_artifact_contracts,
    parse_artifact_contracts,
)
from data_analysts.paths import DataAnalystsContext


ROOT = Path(__file__).resolve().parents[1]


def _registry_payload():
    return json.loads(
        (ROOT / "configs" / "artifact_contracts.json").read_text(encoding="utf-8")
    )


def _universe_specs():
    return json.loads(
        (ROOT / "configs" / "universe_specs.json").read_text(encoding="utf-8")
    )


def _source_family_profiles():
    return json.loads(
        (ROOT / "configs" / "source_family_profiles.json").read_text(
            encoding="utf-8"
        )
    )


def _valid_contract(**overrides):
    contract = {
        "artifact_id": "sample",
        "layer": "raw",
        "base_path": "canonical/raw/sample",
        "file_name": "part.parquet",
        "required_columns": ["date", "ticker"],
        "logical_key": ["date", "ticker"],
        "publication_mode": "partition_upsert",
        "partition_name": "year",
        "partition_field": "date",
        "date_field": "date",
        "availability_field": "date",
        "pit_policy": "source_available_date",
        "source_families": ["sample"],
    }
    contract.update(overrides)
    return contract


def _payload(*contracts, universe_templates=None):
    return {
        "schema_version": "1.0",
        "artifacts": list(contracts),
        "universe_templates": list(universe_templates or []),
    }


def test_daily_tradability_contract_is_partition_upsert():
    context = DataAnalystsContext.from_paths(ROOT)

    contracts = load_artifact_contracts(context, universe_specs={"universes": []})

    contract = contracts["daily_tradability"]
    assert contract.logical_key == ("date", "ticker")
    assert contract.publication_mode == "partition_upsert"
    assert contract.path_for_partition("2026") == (
        "canonical/raw/daily_tradability/year=2026/part.parquet"
    )
    assert contract.inventory_glob() == (
        "canonical/raw/daily_tradability/versions/*/year=*/part.parquet"
    )


def test_contract_is_immutable():
    contract = parse_artifact_contracts(
        _registry_payload(),
        _universe_specs(),
        source_family_profiles=_source_family_profiles(),
    )["daily_tradability"]

    with pytest.raises(FrozenInstanceError):
        contract.layer = "derived"


@pytest.mark.parametrize(
    "base_path",
    [
        "../outside",
        "/outside",
        "C:/outside",
        "C:outside",
        "canonical//sample",
        "canonical/./sample",
    ],
)
def test_contract_rejects_absolute_or_parent_path(base_path):
    with pytest.raises(ArtifactContractError, match="relative"):
        parse_artifact_contracts(
            _payload(_valid_contract(base_path=base_path)), {"universes": []}
        )


@pytest.mark.parametrize("file_name", ["..", ".", "nested/part.parquet"])
def test_contract_rejects_invalid_file_name(file_name):
    with pytest.raises(ArtifactContractError, match="relative file name"):
        parse_artifact_contracts(
            _payload(_valid_contract(file_name=file_name)), {"universes": []}
        )


@pytest.mark.parametrize("publication_mode", ["append", "replace_partition"])
def test_contract_rejects_unknown_publication_mode(publication_mode):
    with pytest.raises(ArtifactContractError, match="publication_mode"):
        parse_artifact_contracts(
            _payload(_valid_contract(publication_mode=publication_mode)),
            {"universes": []},
        )


@pytest.mark.parametrize(
    ("partition_name", "partition_field"),
    [("year", None), (None, "date")],
)
def test_contract_rejects_partition_asymmetry(partition_name, partition_field):
    with pytest.raises(ArtifactContractError, match="partition_name.*partition_field"):
        parse_artifact_contracts(
            _payload(
                _valid_contract(
                    partition_name=partition_name,
                    partition_field=partition_field,
                )
            ),
            {"universes": []},
        )


def test_contract_rejects_empty_logical_key_and_duplicate_id():
    with pytest.raises(ArtifactContractError, match="logical_key"):
        parse_artifact_contracts(
            _payload(_valid_contract(logical_key=[])), {"universes": []}
        )
    with pytest.raises(ArtifactContractError, match="duplicate artifact_id"):
        parse_artifact_contracts(
            _payload(_valid_contract(), _valid_contract()), {"universes": []}
        )


def test_registry_rejects_duplicate_derived_manifest_identity():
    payload = _registry_payload()
    duplicate = dict(payload["universe_templates"][0])
    duplicate["contract_key"] = "universe_{universe_id}:historical_copy"
    payload["universe_templates"].append(duplicate)

    with pytest.raises(ArtifactContractError, match="duplicate manifest identity"):
        parse_artifact_contracts(
            payload,
            {"universes": [{"universe_id": "u"}]},
            source_family_profiles=_source_family_profiles(),
        )


def test_contract_rejects_missing_field():
    contract = _valid_contract()
    del contract["pit_policy"]

    with pytest.raises(ArtifactContractError, match="missing fields.*pit_policy"):
        parse_artifact_contracts(_payload(contract), {"universes": []})


def test_universe_template_expands_only_validated_universe_ids():
    contracts = parse_artifact_contracts(
        _registry_payload(),
        {"universes": [{"universe_id": "tw_common_stock_all"}]},
        source_family_profiles=_source_family_profiles(),
    )

    contract = contracts["universe_tw_common_stock_all:historical"]
    assert contract.base_path.endswith("/tw_common_stock_all/membership_by_year")
    assert "{" not in contract.path_for_partition("2026")


def test_contract_rejects_unexpanded_universe_variables():
    with pytest.raises(ArtifactContractError, match="unexpanded"):
        parse_artifact_contracts(
            _payload(_valid_contract(base_path="canonical/{unknown}/sample")),
            {"universes": []},
        )


def test_registry_covers_pipeline_manifest_artifacts_and_raw_keys_match_profiles():
    context = DataAnalystsContext.from_paths(ROOT)
    universe_specs = json.loads(
        context.config_path("universe_specs.json").read_text(encoding="utf-8")
    )
    profiles = json.loads(
        context.config_path("source_family_profiles.json").read_text(encoding="utf-8")
    )

    contracts = load_artifact_contracts(context, universe_specs)

    expected_static = {
        "security_master",
        "daily_price_volume",
        "trading_calendar",
        "daily_tradability",
        "daily_chip",
        "monthly_sales",
        "financial_statement_raw",
        "self_reported_numbers_raw",
        "taiwan_index_futures_near_month",
        "director_supervisor_holdings",
        "board_reelection_statistics",
        "executive_change_events",
        "merger_acquisition_events",
        "private_placement_relation_events",
        "insider_transfer_completed",
        "insider_transfer_declared_not_completed",
        "treasury_stock_events",
        "financial_statement_pit_selected",
        "self_reported_numbers_pit_selected",
        "dividend_events",
        "capital_action_events",
        "corporate_actions",
        "security_panel",
        "security_panel_history",
    }
    assert expected_static <= contracts.keys()
    for universe in universe_specs["universes"]:
        assert f"universe_{universe['universe_id']}:historical" in contracts
        assert f"universe_{universe['universe_id']}:exact_date" in contracts

    by_family = {item["family_id"]: item for item in profiles["families"]}
    for artifact_id, profile in by_family.items():
        if artifact_id in contracts and contracts[artifact_id].layer == "raw":
            assert contracts[artifact_id].logical_key == tuple(profile["primary_key"])


def test_raw_contract_key_mismatch_is_rejected():
    payload = _registry_payload()
    daily_tradability = next(
        item
        for item in payload["artifacts"]
        if item["artifact_id"] == "daily_tradability"
    )
    daily_tradability["logical_key"] = ["date"]

    with pytest.raises(ArtifactContractError, match="logical_key mismatch"):
        parse_artifact_contracts(
            payload,
            _universe_specs(),
            source_family_profiles=_source_family_profiles(),
        )


@pytest.mark.parametrize("missing_artifact", ["security_panel", "daily_tradability"])
def test_registry_rejects_missing_required_static_artifact(missing_artifact):
    payload = _registry_payload()
    payload["artifacts"] = [
        item for item in payload["artifacts"] if item["artifact_id"] != missing_artifact
    ]

    with pytest.raises(ArtifactContractError, match="missing required artifacts"):
        parse_artifact_contracts(
            payload,
            _universe_specs(),
            source_family_profiles=_source_family_profiles(),
        )


def test_registry_rejects_missing_universe_template_variant():
    payload = _registry_payload()
    payload["universe_templates"] = []

    with pytest.raises(ArtifactContractError, match="universe template"):
        parse_artifact_contracts(
            payload,
            _universe_specs(),
            source_family_profiles=_source_family_profiles(),
        )


def test_registry_validates_universe_templates_when_no_universes_are_configured():
    payload = _registry_payload()
    del payload["universe_templates"][0]["pit_policy"]

    with pytest.raises(ArtifactContractError, match="missing fields.*pit_policy"):
        parse_artifact_contracts(
            payload,
            {"universes": []},
            source_family_profiles=_source_family_profiles(),
        )


def test_registry_rejects_enabled_source_family_without_contract_coverage():
    payload = _registry_payload()
    for item in payload["artifacts"]:
        item["source_families"] = list(
            dict.fromkeys(
                "capital_formation" if family == "dividend_policy" else family
                for family in item["source_families"]
            )
        )

    with pytest.raises(ArtifactContractError, match="enabled source families"):
        parse_artifact_contracts(
            payload,
            _universe_specs(),
            source_family_profiles=_source_family_profiles(),
        )


def test_registry_rejects_enabled_raw_family_without_its_raw_contract():
    payload = _registry_payload()
    payload["artifacts"][0]["source_families"].append("new_raw_family")
    profiles = _source_family_profiles()
    profiles["families"].append(
        {
            "family_id": "new_raw_family",
            "enabled": True,
            "primary_key": ["date", "ticker"],
        }
    )

    with pytest.raises(ArtifactContractError, match="missing raw artifact contract"):
        parse_artifact_contracts(
            payload,
            _universe_specs(),
            source_family_profiles=profiles,
        )


def test_universe_registry_has_distinct_historical_and_exact_date_layouts():
    contracts = load_artifact_contracts(
        DataAnalystsContext.from_paths(ROOT), _universe_specs()
    )

    historical = contracts["universe_tw_common_stock_all:historical"]
    exact_date = contracts["universe_tw_common_stock_all:exact_date"]
    assert historical.artifact_id == exact_date.artifact_id == (
        "universe_tw_common_stock_all"
    )
    assert historical.path_for_partition("2026") == (
        "canonical/derived/universes/tw_common_stock_all/"
        "membership_by_year/as_of_year=2026/part.parquet"
    )
    assert exact_date.path_for_partition("2026-07-20") == (
        "canonical/derived/universes/tw_common_stock_all/"
        "membership_by_date/as_of_date=2026-07-20/membership.parquet"
    )
    assert historical.publication_mode == "partition_upsert"
    assert exact_date.publication_mode == "snapshot_by_value"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"logical_key": ["missing"]}, "logical_key.*required_columns"),
        ({"partition_field": "missing"}, "partition_field.*required_columns"),
        ({"date_field": "missing"}, "date_field.*required_columns"),
        ({"availability_field": "missing"}, "availability_field.*required_columns"),
        ({"source_families": []}, "source_families"),
    ],
)
def test_contract_rejects_fields_not_backed_by_required_columns(overrides, message):
    with pytest.raises(ArtifactContractError, match=message):
        parse_artifact_contracts(
            _payload(_valid_contract(**overrides)),
            {"universes": []},
        )


def test_expected_outputs_are_registry_derived_and_transitive():
    contracts = load_artifact_contracts(
        DataAnalystsContext.from_paths(ROOT), _universe_specs()
    )

    outputs = expected_contract_outputs(
        contracts, {"financial_statement_raw", "dividend_policy"}
    )

    assert {
        "financial_statement_raw",
        "financial_statement_pit_selected",
    }.issubset(outputs["financial_statement_raw"])
    assert {
        "dividend_events",
        "corporate_actions",
        "daily_price_volume",
        "security_panel",
        "security_panel_history",
        "universe_tw_common_stock_all:historical",
        "universe_tw_common_stock_all:exact_date",
    }.issubset(outputs["dividend_policy"])


def test_registry_rejects_unknown_dependency_token():
    payload = _registry_payload()
    corporate = next(
        item
        for item in payload["artifacts"]
        if item["artifact_id"] == "corporate_actions"
    )
    corporate["source_families"].append("typo_dependency")

    with pytest.raises(ArtifactContractError, match="unknown or ambiguous"):
        parse_artifact_contracts(
            payload,
            _universe_specs(),
            source_family_profiles=_source_family_profiles(),
        )
