import json
from dataclasses import replace

import pytest

import data_analysts.dataset_publication as publication_module
from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.artifacts import ArtifactError
from data_analysts.dataset_publication import publish_dataset
from data_analysts.paths import DataAnalystsContext


def _contracts() -> tuple[ArtifactContract, ArtifactContract]:
    historical = ArtifactContract(
        contract_key="universe_u:historical", artifact_id="universe_u",
        variant="historical", layer="derived",
        base_path="canonical/derived/universes/u/membership_by_year",
        file_name="part.parquet",
        required_columns=(
            "as_of_date", "effective_date", "universe_id", "ticker", "rank",
            "data_cutoff_at",
        ),
        logical_key=("as_of_date", "universe_id", "ticker"),
        publication_mode="partition_upsert", partition_name="as_of_year",
        partition_field="as_of_date", date_field="as_of_date",
        availability_field="effective_date",
        pit_policy="effective_next_trading_day_membership",
        source_families=("security_panel_history",),
    )
    exact = ArtifactContract(
        contract_key="universe_u:exact_date", artifact_id="universe_u",
        variant="exact_date", layer="derived",
        base_path="canonical/derived/universes/u/membership_by_date",
        file_name="membership.parquet",
        required_columns=(
            "as_of_date", "universe_id", "ticker", "rank", "data_cutoff_at",
        ),
        logical_key=("as_of_date", "universe_id", "ticker"),
        publication_mode="snapshot_by_value", partition_name="as_of_date",
        partition_field="as_of_date", date_field="as_of_date",
        availability_field="as_of_date", pit_policy="decision_date_membership",
        source_families=("security_panel",),
    )
    return historical, exact


def _row(*, historical: bool, as_of_date: str = "2026-07-08") -> dict[str, object]:
    row: dict[str, object] = {
        "as_of_date": as_of_date, "universe_id": "u", "ticker": "2330",
        "rank": 1, "data_cutoff_at": f"{as_of_date}T12:00:00Z",
    }
    if historical:
        row["effective_date"] = "2026-07-09"
    return row


def _convert_to_legacy(context, contract, manifest):
    payload = dict(manifest)
    payload.pop("contract_key")
    payload.pop("variant")
    legacy = context.store_path("manifests", f"{contract.artifact_id}.json")
    legacy_bytes = json.dumps(payload, indent=2, sort_keys=True).encode()
    legacy.write_bytes(legacy_bytes)
    context.store_path("manifests", contract.manifest_file_name).unlink()
    return legacy, legacy_bytes


@pytest.mark.parametrize("selected", ["historical", "exact_date"])
def test_migrates_unique_legacy_variant_from_manifest_listed_parquet(tmp_path, selected):
    context = DataAnalystsContext.from_paths(tmp_path)
    historical, exact = _contracts()
    contract = historical if selected == "historical" else exact
    result = publish_dataset(
        context, contract, [_row(historical=selected == "historical")],
        "full_history" if selected == "historical" else "daily",
    )
    legacy, _ = _convert_to_legacy(context, contract, result.manifest)

    migrated = publication_module.migrate_legacy_variant_manifests(
        context, [historical, exact]
    )

    target = context.store_path("manifests", contract.manifest_file_name)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert migrated == [contract.contract_key]
    assert payload["contract_key"] == contract.contract_key
    assert payload["variant"] == contract.variant
    assert payload["artifact_paths"] == result.manifest["artifact_paths"]
    assert not legacy.exists()


def test_migrates_live_like_22_partition_historical_manifest(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    historical, exact = _contracts()
    rows = [
        _row(historical=True, as_of_date=f"{year}-07-08")
        for year in range(2005, 2027)
    ]
    result = publish_dataset(context, historical, rows, "full_history")
    legacy, _ = _convert_to_legacy(context, historical, result.manifest)

    publication_module.migrate_legacy_variant_manifests(
        context, [historical, exact]
    )

    payload = json.loads(
        context.store_path("manifests", historical.manifest_file_name).read_text()
    )
    assert len(payload["artifact_paths"]) == 22
    assert payload["partitioning"] == ["as_of_year"]
    assert all("membership_by_year" in path for path in payload["artifact_paths"])
    assert not legacy.exists()


def test_migration_rejects_mixed_legacy_paths_without_mutation(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    historical, exact = _contracts()
    historical_result = publish_dataset(
        context, historical, [_row(historical=True)], "full_history"
    )
    exact_result = publish_dataset(context, exact, [_row(historical=False)], "daily")
    payload = dict(historical_result.manifest)
    payload.pop("contract_key")
    payload.pop("variant")
    payload["artifact_paths"] = [
        *historical_result.manifest["artifact_paths"],
        *exact_result.manifest["artifact_paths"],
    ]
    legacy = context.store_path("manifests", "universe_u.json")
    legacy_bytes = json.dumps(payload, sort_keys=True).encode()
    legacy.write_bytes(legacy_bytes)
    historical_result.manifest_path.unlink()
    exact_result.manifest_path.unlink()

    with pytest.raises(ArtifactError, match="mixed or no contract match"):
        publication_module.migrate_legacy_variant_manifests(
            context, [historical, exact]
        )

    assert legacy.read_bytes() == legacy_bytes
    assert not historical_result.manifest_path.exists()
    assert not exact_result.manifest_path.exists()


def test_migration_rejects_ambiguous_contract_templates(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    historical, exact = _contracts()
    result = publish_dataset(context, exact, [_row(historical=False)], "daily")
    legacy, legacy_bytes = _convert_to_legacy(context, exact, result.manifest)
    overlapping = replace(
        exact, contract_key="universe_u:overlap", variant="overlap"
    )

    with pytest.raises(ArtifactError, match="ambiguous contract match"):
        publication_module.migrate_legacy_variant_manifests(
            context, [historical, exact, overlapping]
        )

    assert legacy.read_bytes() == legacy_bytes
    assert not result.manifest_path.exists()


def test_migration_rejects_manifest_evidence_mismatch_without_mutation(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    historical, exact = _contracts()
    result = publish_dataset(
        context, historical, [_row(historical=True)], "full_history"
    )
    legacy, _ = _convert_to_legacy(context, historical, result.manifest)
    payload = json.loads(legacy.read_text(encoding="utf-8"))
    payload["row_count"] += 1
    legacy_bytes = json.dumps(payload, sort_keys=True).encode()
    legacy.write_bytes(legacy_bytes)

    with pytest.raises(ArtifactError, match="evidence mismatch.*row_count"):
        publication_module.migrate_legacy_variant_manifests(
            context, [historical, exact]
        )

    assert legacy.read_bytes() == legacy_bytes
    assert not result.manifest_path.exists()


def test_migration_write_failure_keeps_original_bytes_and_removes_partial_target(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    historical, exact = _contracts()
    result = publish_dataset(
        context, historical, [_row(historical=True)], "full_history"
    )
    legacy, legacy_bytes = _convert_to_legacy(context, historical, result.manifest)

    def partial_write(path, text):
        path.write_text(text, encoding="utf-8")
        raise PermissionError("synthetic migration write failure")

    monkeypatch.setattr(publication_module, "atomic_write_text", partial_write)

    with pytest.raises(ArtifactError, match="migration failed"):
        publication_module.migrate_legacy_variant_manifests(
            context, [historical, exact]
        )

    assert legacy.read_bytes() == legacy_bytes
    assert not result.manifest_path.exists()
