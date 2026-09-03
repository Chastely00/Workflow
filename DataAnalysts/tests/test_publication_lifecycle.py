from __future__ import annotations

import json
from pathlib import Path

import pytest

import data_analysts.cli as cli_module
from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.artifacts import ArtifactError
from data_analysts.cli import main
from data_analysts.config import load_runtime_config
from data_analysts.dataset_publication import publish_dataset
from data_analysts.paths import DataAnalystsContext
from data_analysts.pipeline import run_pipeline
from data_analysts.store_audit import audit_store


class _FakeCollection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def find(self, query=None):
        return list(self.rows)


class _FakeDatabase:
    def __init__(self, rows_by_collection: dict[str, list[dict[str, object]]]) -> None:
        self.collections = {
            name: _FakeCollection(rows) for name, rows in rows_by_collection.items()
        }

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections[name]

    def list_collection_names(self) -> list[str]:
        return list(self.collections)


def _copy_configs(root: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "configs"
    target = root / "configs"
    target.mkdir(parents=True)
    for path in source.glob("*.json"):
        (target / path.name).write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )


def _detach_panel_history_dependency(root: Path, family_id: str) -> None:
    path = root / "configs" / "artifact_contracts.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    panel = next(
        item
        for item in payload["artifacts"]
        if item["artifact_id"] == "security_panel_history"
    )
    panel["source_families"] = [
        source
        for source in panel["source_families"]
        if source != family_id
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")


def _configure_tradability_only(root: Path) -> None:
    _copy_configs(root)
    _detach_panel_history_dependency(root, "daily_tradability")
    profile_path = root / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["families"] = [
        {
            "family_id": "daily_tradability",
            "enabled": True,
            "connection": "apistkattr",
            "collection_pattern": "{ticker}",
            "source_profile": "large_daily_panel",
            "primary_key": ["date", "ticker"],
            "date_fields": {"source_date": "mdate"},
            "availability": {"type": "source_available_date", "field": "mdate"},
            "partitioning": ["year"],
            "pit_policy": "source_available_date",
        }
    ]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")


def _source_row(value: str, *, tradable: bool = True) -> dict[str, object]:
    return {
        "coid": "2330",
        "mdate": value,
        "tradable": tradable,
        "source_row_id": f"2330:{value}",
        "data_cutoff_at": f"{value}T12:00:00Z",
    }


def _row_for_contract(
    contract: ArtifactContract,
    value: str,
    *, ticker: str = "2330",
) -> dict[str, object]:
    date_fields = {
        field
        for field in (
            contract.partition_field,
            contract.date_field,
            contract.availability_field,
        )
        if field
    }
    numeric_fields = {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "traded_value",
        "adj_factor",
        "adj_close",
        "cash_dividend_per_share",
        "stock_dividend_ratio",
        "share_multiplier",
        "cash_return_per_share",
        "cash_amount",
        "market_cap",
        "adv20",
        "rank",
    }
    boolean_fields = {"is_trading_day", "tradable", "included"}
    row: dict[str, object] = {}
    for field in contract.required_columns:
        if field in date_fields or field.endswith("_date") or field == "date":
            row[field] = value
        elif field == "data_cutoff_at":
            row[field] = f"{value}T12:00:00Z"
        elif field == "ticker":
            row[field] = ticker
        elif field in numeric_fields:
            row[field] = 1.0
        elif field in boolean_fields:
            row[field] = True
        elif field == "universe_id":
            row[field] = contract.artifact_id.removeprefix("universe_")
        else:
            row[field] = f"{field}:{ticker}"
    # Publication validation requires lineage even for older registry entries
    # whose required_columns predate the explicit cutoff contract.
    row.setdefault("data_cutoff_at", f"{value}T12:00:00Z")
    return row


def _manifest(context: DataAnalystsContext, contract: ArtifactContract) -> dict:
    return json.loads(
        context.store_path("manifests", contract.manifest_file_name).read_text(
            encoding="utf-8"
        )
    )


def test_fake_apistkattr_full_backfill_daily_exact_rerun_preserves_tradability(
    tmp_path,
):
    _configure_tradability_only(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    full_database = _FakeDatabase(
        {"2330": [_source_row("2024-12-31"), _source_row("2025-01-02")]}
    )

    run_pipeline(
        context,
        config,
        families={"daily_tradability"},
        mongo_databases={"apistkattr": full_database},
        run_scope="full_history",
    )
    run_pipeline(
        context,
        config,
        families={"daily_tradability"},
        start_date="2025-01-03",
        end_date="2025-01-03",
        mongo_databases={
            "apistkattr": _FakeDatabase({"2330": [_source_row("2025-01-03")]})
        },
        run_scope="bounded_backfill",
    )
    for _ in range(2):
        run_pipeline(
            context,
            config,
            families={"daily_tradability"},
            as_of_date="2025-01-06",
            mongo_databases={
                "apistkattr": _FakeDatabase(
                    {"2330": [_source_row("2025-01-06", tradable=False)]}
                )
            },
            run_scope="daily",
        )

    contract = config.artifact_contracts["daily_tradability"]
    evidence = audit_store(context, {contract.contract_key: contract})
    artifact = evidence["artifacts"][contract.contract_key]
    manifest = _manifest(context, contract)

    assert evidence["status"] == "ready", evidence
    assert artifact["date_range"] == ["2024-12-31", "2025-01-06"]
    assert evidence["metrics"]["duplicate_logical_key_count"] == 0
    assert evidence["metrics"]["orphan_partition_count"] == 0
    assert manifest["row_count"] == 4
    assert len(manifest["artifact_paths"]) == 2


def _yearly_contracts(config) -> list[ArtifactContract]:
    return [
        contract
        for contract in config.artifact_contracts.values()
        if contract.publication_mode == "partition_upsert"
    ]


def test_every_year_partitioned_contract_preserves_coverage_across_run_scopes(tmp_path):
    _copy_configs(tmp_path)
    config = load_runtime_config(DataAnalystsContext.from_paths(tmp_path))

    for contract in _yearly_contracts(config):
        case_root = tmp_path / contract.contract_key.replace(":", "-")
        context = DataAnalystsContext.from_paths(case_root)
        publish_dataset(
            context,
            contract,
            [_row_for_contract(contract, "2024-12-31")],
            "full_history",
        )
        before = _manifest(context, contract)
        publish_dataset(
            context,
            contract,
            [_row_for_contract(contract, "2025-01-02")],
            "bounded_backfill",
        )
        publish_dataset(
            context,
            contract,
            [_row_for_contract(contract, "2025-01-03")],
            "daily",
        )
        publish_dataset(
            context,
            contract,
            [_row_for_contract(contract, "2025-01-03")],
            "daily",
        )
        after = _manifest(context, contract)
        evidence = audit_store(context, {contract.contract_key: contract})

        assert after["date_range"][0] == before["date_range"][0]
        assert after["row_count"] == 3
        assert evidence["status"] == "ready", (
            contract.contract_key,
            evidence["issues"],
        )
        assert evidence["metrics"]["duplicate_logical_key_count"] == 0
        assert evidence["metrics"]["orphan_partition_count"] == 0


def test_every_partition_upsert_contract_full_history_removes_source_deleted_partitions(
    tmp_path,
):
    _copy_configs(tmp_path)
    config = load_runtime_config(DataAnalystsContext.from_paths(tmp_path))

    for contract in _yearly_contracts(config):
        context = DataAnalystsContext.from_paths(
            tmp_path / f"replacement-{contract.contract_key.replace(':', '-')}"
        )
        publish_dataset(
            context,
            contract,
            [_row_for_contract(contract, "2024-12-31"), _row_for_contract(contract, "2025-01-02")],
            "full_history",
        )
        publish_dataset(
            context,
            contract,
            [_row_for_contract(contract, "2025-01-03")],
            "full_history",
        )

        manifest = _manifest(context, contract)
        evidence = audit_store(context, {contract.contract_key: contract})
        assert len(manifest["artifact_paths"]) == 1
        assert "/versions/" in manifest["artifact_paths"][0]
        assert f"/{contract.partition_name}=2025/" in manifest["artifact_paths"][0]
        assert manifest["row_count"] == 1
        assert not any(
            f"/{contract.partition_name}=2024/" in path
            for path in manifest["artifact_paths"]
        )
        assert evidence["status"] == "ready", (contract.contract_key, evidence["issues"])


def test_allow_empty_partition_contracts_publish_explicit_empty_active_inventory(tmp_path):
    _copy_configs(tmp_path)
    config = load_runtime_config(DataAnalystsContext.from_paths(tmp_path))
    contracts = [contract for contract in _yearly_contracts(config) if contract.allow_empty]
    assert contracts

    for contract in contracts:
        context = DataAnalystsContext.from_paths(
            tmp_path / f"empty-{contract.contract_key.replace(':', '-')}"
        )
        first = publish_dataset(
            context, contract, [_row_for_contract(contract, "2025-01-02")],
            "full_history",
        )
        retained = context.artifact_path(first.manifest["artifact_paths"][0])

        publish_dataset(context, contract, [], "full_history")

        manifest = _manifest(context, contract)
        evidence = audit_store(context, {contract.contract_key: contract})
        assert manifest["artifact_paths"] == []
        assert manifest["row_count"] == 0
        assert retained.is_file()
        assert evidence["status"] == "ready", (contract.contract_key, evidence["issues"])


def test_disallow_empty_partition_contracts_fail_without_switching_manifest(tmp_path):
    _copy_configs(tmp_path)
    config = load_runtime_config(DataAnalystsContext.from_paths(tmp_path))
    contracts = [contract for contract in _yearly_contracts(config) if not contract.allow_empty]
    assert contracts

    for contract in contracts:
        context = DataAnalystsContext.from_paths(
            tmp_path / f"required-{contract.contract_key.replace(':', '-')}"
        )
        publish_dataset(
            context, contract, [_row_for_contract(contract, "2025-01-02")],
            "full_history",
        )
        manifest_path = context.store_path("manifests", contract.manifest_file_name)
        before = manifest_path.read_bytes()

        with pytest.raises(ArtifactError, match="does not allow empty"):
            publish_dataset(context, contract, [], "full_history")

        assert manifest_path.read_bytes() == before


def test_full_replace_snapshot_and_all_universe_variants_have_distinct_complete_manifests(
    tmp_path,
):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    full_replace = [
        contract
        for contract in config.artifact_contracts.values()
        if contract.publication_mode == "full_replace"
    ]
    snapshots = [
        contract
        for contract in config.artifact_contracts.values()
        if contract.publication_mode == "snapshot_by_value"
    ]

    for contract in full_replace:
        publish_dataset(
            context,
            contract,
            [_row_for_contract(contract, "2025-01-02")],
            "full_history",
        )
        first = _manifest(context, contract)
        publish_dataset(
            context,
            contract,
            [_row_for_contract(contract, "2025-01-03")],
            "full_history",
        )
        second = _manifest(context, contract)
        assert first["artifact_paths"] != second["artifact_paths"]
        assert second["row_count"] == 1

    for contract in snapshots:
        for value in ("2025-01-02", "2025-01-03", "2025-01-03"):
            publish_dataset(
                context,
                contract,
                [_row_for_contract(contract, value)],
                "daily",
            )
        manifest = _manifest(context, contract)
        assert manifest["row_count"] == 2
        assert len(manifest["artifact_paths"]) == 2

    universe_contracts = {
        contract.contract_key: contract
        for contract in config.artifact_contracts.values()
        if contract.artifact_id.startswith("universe_")
    }
    expected_universes = {
        item["universe_id"]
        for item in config.universe_specs["universes"]
        if item.get("enabled", True)
    }
    assert {
        contract.artifact_id.removeprefix("universe_")
        for contract in universe_contracts.values()
    } == expected_universes
    assert {contract.variant for contract in universe_contracts.values()} == {
        "historical",
        "exact_date",
    }
    assert len({contract.manifest_file_name for contract in universe_contracts.values()}) == len(
        universe_contracts
    )


def test_failed_incremental_validation_keeps_manifest_and_coverage(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    contract = config.artifact_contracts["daily_tradability"]
    publish_dataset(
        context,
        contract,
        [_row_for_contract(contract, "2024-12-31")],
        "full_history",
    )
    before = context.store_path(
        "manifests", contract.manifest_file_name
    ).read_bytes()

    invalid = _row_for_contract(contract, "2025-01-02")
    invalid.pop("data_cutoff_at")
    with pytest.raises(ArtifactError, match="data_cutoff_at"):
        publish_dataset(context, contract, [invalid], "daily")

    assert context.store_path("manifests", contract.manifest_file_name).read_bytes() == before
    evidence = audit_store(context, {contract.contract_key: contract})
    assert evidence["status"] == "ready"
    assert evidence["artifacts"][contract.contract_key]["date_range"] == [
        "2024-12-31",
        "2024-12-31",
    ]


def test_no_arg_cli_scheduler_uses_versioned_calendar_manifest(tmp_path, monkeypatch):
    _copy_configs(tmp_path)
    _detach_panel_history_dependency(tmp_path, "trading_calendar")
    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
    calendar_profile = next(
        profile
        for profile in profile_payload["families"]
        if profile["family_id"] == "trading_calendar"
    )
    calendar_profile["fixture_rows"] = [
        {
            "zdate": "2026-07-17",
            "mkt": "TWSE",
            "date_rmk": "",
            "data_cutoff_at": "2026-07-17T12:00:00Z",
        }
    ]
    profile_payload["families"] = [calendar_profile]
    profile_path.write_text(json.dumps(profile_payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    calendar = config.artifact_contracts["trading_calendar"]
    run_pipeline(
        context,
        config,
        families={"trading_calendar"},
        run_scope="full_history",
    )
    assert "/versions/" in _manifest(context, calendar)["artifact_paths"][0]
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "load_runtime_config", lambda loaded: config)
    monkeypatch.setattr(
        cli_module,
        "audit_store",
        lambda loaded, contracts: {"status": "ready", "artifacts": {}},
    )

    def run(loaded, runtime_config, **kwargs):
        calls.append(kwargs["as_of_date"])
        return {"status": "ready", "as_of_date": kwargs["as_of_date"], "families": []}

    monkeypatch.setattr(cli_module, "run_pipeline", run)
    monkeypatch.setattr(
        cli_module,
        "verify_runtime",
        lambda loaded, as_of_date=None, **kwargs: {"status": "ready"},
    )

    result = main(["run-daily", "--project-root", str(tmp_path)])

    assert result == 0
    assert calls == ["2026-07-17"]
