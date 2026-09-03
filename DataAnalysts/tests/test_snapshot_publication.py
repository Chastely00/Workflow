import json
from dataclasses import replace

import pyarrow.parquet as pq
import pytest

from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.artifacts import ArtifactError
from data_analysts.dataset_publication import publish_dataset
from data_analysts.paths import DataAnalystsContext


@pytest.fixture
def context(tmp_path) -> DataAnalystsContext:
    return DataAnalystsContext.from_paths(tmp_path)


@pytest.fixture
def panel_contract() -> ArtifactContract:
    return ArtifactContract(
        contract_key="security_panel",
        artifact_id="security_panel",
        variant="static",
        layer="derived",
        base_path="canonical/derived/security_panel",
        file_name="security_panel.parquet",
        required_columns=(
            "as_of_date",
            "source_max_date",
            "ticker",
            "tradable",
            "adj_close",
            "market_cap",
            "adv20",
            "data_cutoff_at",
        ),
        logical_key=("as_of_date", "ticker"),
        publication_mode="snapshot_by_value",
        partition_name="as_of_date",
        partition_field="as_of_date",
        date_field="as_of_date",
        availability_field="as_of_date",
        pit_policy="decision_date_panel",
        source_families=("daily_price_volume", "security_master"),
    )


def panel_rows(as_of_date: str, *, adj_close: float = 100.0):
    return [
        {
            "as_of_date": as_of_date,
            "source_max_date": as_of_date,
            "ticker": "2330",
            "tradable": True,
            "adj_close": adj_close,
            "market_cap": 1_000_000.0,
            "adv20": 50_000.0,
            "data_cutoff_at": f"{as_of_date}T10:00:00Z",
        }
    ]


def test_new_snapshot_keeps_prior_snapshot_in_manifest(context, panel_contract):
    publish_dataset(context, panel_contract, panel_rows("2026-07-07"), "daily")
    result = publish_dataset(
        context, panel_contract, panel_rows("2026-07-08"), "daily"
    )

    active_version = result.manifest["active_version"]
    assert result.manifest["artifact_paths"] == [
        "canonical/derived/security_panel/"
        f"versions/{active_version}/as_of_date=2026-07-07/security_panel.parquet",
        "canonical/derived/security_panel/"
        f"versions/{active_version}/as_of_date=2026-07-08/security_panel.parquet",
    ]
    assert result.total_row_count == 2


def test_full_history_snapshot_keeps_prior_distinct_snapshot(
    context, panel_contract
):
    publish_dataset(
        context, panel_contract, panel_rows("2026-07-07"), "full_history"
    )

    result = publish_dataset(
        context, panel_contract, panel_rows("2026-07-08"), "full_history"
    )

    assert len(result.manifest["artifact_paths"]) == 2
    assert result.total_row_count == 2


def test_full_history_snapshot_replaces_same_value_only(context, panel_contract):
    publish_dataset(
        context,
        panel_contract,
        panel_rows("2026-07-07", adj_close=90.0),
        "full_history",
    )
    publish_dataset(
        context, panel_contract, panel_rows("2026-07-08"), "full_history"
    )

    result = publish_dataset(
        context,
        panel_contract,
        panel_rows("2026-07-07", adj_close=91.0),
        "full_history",
    )

    rows_by_date = {
        pq.ParquetFile(context.artifact_path(path)).read().to_pylist()[0][
            "as_of_date"
        ]: pq.ParquetFile(context.artifact_path(path)).read().to_pylist()[0]
        for path in result.manifest["artifact_paths"]
    }
    assert rows_by_date["2026-07-07"]["adj_close"] == 91.0
    assert set(rows_by_date) == {"2026-07-07", "2026-07-08"}


@pytest.mark.parametrize("run_scope", ["full_history", "bounded_backfill", "daily"])
def test_allow_empty_snapshot_publishes_no_parquet_in_every_scope(
    context, panel_contract, run_scope
):
    contract = replace(panel_contract, allow_empty=True)
    result = publish_dataset(
        context,
        contract,
        [],
        run_scope,
        snapshot_value="2026-07-07",
    )

    assert result.manifest["artifact_paths"] == []
    assert result.manifest["row_count"] == 0
    assert result.manifest["columns"] == list(contract.required_columns)
    assert not list(context.artifact_path(contract.base_path).rglob("*.parquet"))

    populated = publish_dataset(
        context, contract, panel_rows("2026-07-08"), "daily"
    )
    assert populated.total_row_count == 1


def test_exact_rerun_replaces_only_requested_snapshot(context, panel_contract):
    first = publish_dataset(
        context, panel_contract, panel_rows("2026-07-07", adj_close=90.0), "daily"
    )
    prior_path = context.artifact_path(first.manifest["artifact_paths"][0])
    prior_bytes = prior_path.read_bytes()
    publish_dataset(
        context, panel_contract, panel_rows("2026-07-08", adj_close=100.0), "daily"
    )

    result = publish_dataset(
        context, panel_contract, panel_rows("2026-07-08", adj_close=101.0), "daily"
    )
    rerun_path = context.artifact_path(result.manifest["artifact_paths"][1])

    assert prior_path.read_bytes() == prior_bytes
    assert pq.ParquetFile(rerun_path).read().to_pylist()[0]["adj_close"] == 101.0
    assert result.total_row_count == 2


def test_failed_snapshot_keeps_previous_snapshot_and_manifest(
    context, panel_contract, monkeypatch
):
    first = publish_dataset(
        context, panel_contract, panel_rows("2026-07-07"), "daily"
    )
    manifest_bytes = first.manifest_path.read_bytes()
    path = context.artifact_path(first.manifest["artifact_paths"][0])
    snapshot_bytes = path.read_bytes()
    from data_analysts import dataset_publication

    def fail_validation(*args, **kwargs):
        raise ArtifactError("staged dataset validation failed")

    monkeypatch.setattr(
        dataset_publication, "validate_staged_dataset", fail_validation
    )
    with pytest.raises(ArtifactError, match="staged dataset"):
        publish_dataset(
            context, panel_contract, panel_rows("2026-07-07", adj_close=101.0), "daily"
        )

    assert first.manifest_path.read_bytes() == manifest_bytes
    assert path.read_bytes() == snapshot_bytes


def test_snapshot_requires_one_partition_value(context, panel_contract):
    with pytest.raises(ArtifactError, match="exactly one snapshot"):
        publish_dataset(
            context,
            panel_contract,
            panel_rows("2026-07-07") + panel_rows("2026-07-08"),
            "daily",
        )


def test_failed_snapshot_manifest_build_cleans_staging_and_preserves_manifest(
    context, panel_contract
):
    global_key_contract = replace(panel_contract, logical_key=("ticker",))
    first = publish_dataset(
        context, global_key_contract, panel_rows("2026-07-07"), "daily"
    )
    before_manifest = first.manifest_path.read_bytes()

    with pytest.raises(ArtifactError, match="duplicate logical key across partitions"):
        publish_dataset(
            context, global_key_contract, panel_rows("2026-07-08"), "daily"
        )

    assert first.manifest_path.read_bytes() == before_manifest
    assert not any(context.store_path("staging").rglob("*.parquet"))


def test_exact_date_inventory_does_not_mix_historical_year_layout(
    context, panel_contract
):
    exact = replace(
        panel_contract,
        contract_key="universe_tw_common_stock_all:exact_date",
        artifact_id="universe_tw_common_stock_all",
        variant="exact_date",
        base_path=(
            "canonical/derived/universes/tw_common_stock_all/membership_by_date"
        ),
        file_name="membership.parquet",
    )
    historical_path = context.artifact_path(
        "canonical/derived/universes/tw_common_stock_all/"
        "membership_by_year/as_of_year=2026/part.parquet"
    )
    historical_path.parent.mkdir(parents=True)
    historical_path.write_bytes(b"not part of exact-date inventory")

    result = publish_dataset(context, exact, panel_rows("2026-07-08"), "daily")

    active_version = result.manifest["active_version"]
    assert result.manifest["artifact_paths"] == [
        "canonical/derived/universes/tw_common_stock_all/"
        "membership_by_date/"
        f"versions/{active_version}/as_of_date=2026-07-08/membership.parquet"
    ]
    assert "membership_by_year" not in json.dumps(result.manifest)


def test_variant_publication_fails_closed_on_invalid_legacy_manifest(
    context, panel_contract
):
    exact = replace(
        panel_contract,
        contract_key="universe_tw_common_stock_all:exact_date",
        artifact_id="universe_tw_common_stock_all",
        variant="exact_date",
        base_path=(
            "canonical/derived/universes/tw_common_stock_all/membership_by_date"
        ),
        file_name="membership.parquet",
    )
    legacy = context.store_path("manifests", "universe_tw_common_stock_all.json")
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"artifact_id":"universe_tw_common_stock_all"}')

    with pytest.raises(ArtifactError, match="invalid artifact_paths"):
        publish_dataset(context, exact, panel_rows("2026-07-08"), "daily")

    assert not context.store_path("manifests", exact.manifest_file_name).exists()
