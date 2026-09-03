import json

import pyarrow.parquet as pq
import pytest

from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.artifacts import ArtifactError
from data_analysts.dataset_publication import publish_dataset, reconstruct_manifest
from data_analysts.paths import DataAnalystsContext


@pytest.fixture
def context(tmp_path) -> DataAnalystsContext:
    return DataAnalystsContext.from_paths(tmp_path)


@pytest.fixture
def contract() -> ArtifactContract:
    return ArtifactContract(
        contract_key="trading_calendar",
        artifact_id="trading_calendar",
        variant="static",
        layer="raw",
        base_path="canonical/raw/trading_calendar",
        file_name="trading_calendar.parquet",
        required_columns=(
            "date",
            "market",
            "is_trading_day",
            "source_available_date",
            "data_cutoff_at",
        ),
        logical_key=("date", "market"),
        publication_mode="full_replace",
        partition_name=None,
        partition_field=None,
        date_field="date",
        availability_field="source_available_date",
        pit_policy="source_available_date",
        source_families=("trading_calendar",),
    )


def calendar_rows(*dates: str) -> list[dict[str, object]]:
    return [
        {
            "date": value,
            "market": "TWSE",
            "is_trading_day": True,
            "source_available_date": value,
            "data_cutoff_at": f"{value}T10:00:00Z",
        }
        for value in dates
    ]


def _manifest(result) -> dict[str, object]:
    return json.loads(result.manifest_path.read_text(encoding="utf-8"))


def _visible_rows(context, result) -> list[dict[str, object]]:
    path = context.artifact_path(_manifest(result)["artifact_paths"][0])
    return pq.ParquetFile(path).read().to_pylist()


def test_successful_full_replace_switches_manifest_and_keeps_prior_version(
    context, contract
):
    first = publish_dataset(
        context, contract, calendar_rows("2026-07-07"), "full_history"
    )
    first_path = context.artifact_path(_manifest(first)["artifact_paths"][0])

    second = publish_dataset(
        context, contract, calendar_rows("2026-07-08", "2026-07-09"), "full_history"
    )
    second_manifest = _manifest(second)
    second_path = context.artifact_path(second_manifest["artifact_paths"][0])

    assert first_path != second_path
    assert first_path.exists()
    assert second_path.exists()
    assert [row["date"] for row in _visible_rows(context, second)] == [
        "2026-07-08",
        "2026-07-09",
    ]
    assert second_manifest["row_count"] == 2
    assert second.manifest == second_manifest


def test_first_full_replace_can_reconstruct_from_manifest_active_version(
    context, contract
):
    result = publish_dataset(
        context, contract, calendar_rows("2026-07-07"), "full_history"
    )

    reconstructed = reconstruct_manifest(context, contract)

    assert reconstructed["artifact_paths"] == result.manifest["artifact_paths"]
    assert reconstructed["row_count"] == 1


def test_reconstruct_lists_only_new_active_version_and_allows_retained_old_version(
    context, contract
):
    first = publish_dataset(
        context, contract, calendar_rows("2026-07-07"), "full_history"
    )
    old_path = context.artifact_path(first.manifest["artifact_paths"][0])
    second = publish_dataset(
        context, contract, calendar_rows("2026-07-08"), "full_history"
    )

    reconstructed = reconstruct_manifest(context, contract)

    assert old_path.exists()
    assert reconstructed["artifact_paths"] == second.manifest["artifact_paths"]
    assert first.manifest["artifact_paths"][0] not in reconstructed["artifact_paths"]


def test_full_replace_reconstruct_blocks_rogue_parquet_outside_versions_layout(
    context, contract
):
    publish_dataset(context, contract, calendar_rows("2026-07-07"), "full_history")
    rogue = context.artifact_path(f"{contract.base_path}/rogue.parquet")
    rogue.write_bytes(b"not a valid retained version")

    with pytest.raises(ArtifactError, match="orphan parquet.*rogue.parquet"):
        reconstruct_manifest(context, contract)


def test_failed_full_replace_keeps_previous_manifest_and_rows(
    context, contract, monkeypatch
):
    first = publish_dataset(
        context, contract, calendar_rows("2026-07-07"), "full_history"
    )
    before_manifest = first.manifest_path.read_bytes()
    before_rows = _visible_rows(context, first)

    from data_analysts import dataset_publication

    def fail_validation(*args, **kwargs):
        raise ArtifactError("staged dataset validation failed")

    monkeypatch.setattr(
        dataset_publication, "validate_staged_dataset", fail_validation
    )
    with pytest.raises(ArtifactError, match="staged dataset"):
        publish_dataset(
            context,
            contract,
            calendar_rows("2026-07-08", "2026-07-09"),
            "full_history",
        )

    assert first.manifest_path.read_bytes() == before_manifest
    assert _visible_rows(context, first) == before_rows
    staging_root = context.store_path(".staging", contract.artifact_id)
    assert not staging_root.exists() or not list(staging_root.iterdir())


def test_full_replace_rejects_bounded_scope_before_writing(context, contract):
    with pytest.raises(ArtifactError, match="full_replace.*full_history"):
        publish_dataset(context, contract, calendar_rows("2026-07-08"), "daily")

    assert not context.data_store.exists()


def test_committed_partition_cow_retains_old_immutable_version(context):
    contract = ArtifactContract(
        contract_key="daily_tradability",
        artifact_id="daily_tradability",
        variant="static",
        layer="raw",
        base_path="canonical/raw/daily_tradability",
        file_name="part.parquet",
        required_columns=("date", "ticker", "source_available_date", "data_cutoff_at"),
        logical_key=("date", "ticker"),
        publication_mode="partition_upsert",
        partition_name="year",
        partition_field="date",
        date_field="date",
        availability_field="source_available_date",
        pit_policy="source_available_date",
        source_families=("daily_tradability",),
    )
    original = {
        "date": "2026-07-07",
        "ticker": "2330",
        "source_available_date": "2026-07-07",
        "data_cutoff_at": "2026-07-07T10:00:00Z",
    }
    corrected = dict(original, data_cutoff_at="2026-07-07T12:00:00Z")
    first = publish_dataset(context, contract, [original], "full_history")
    old_path = context.artifact_path(first.manifest["artifact_paths"][0])
    old_bytes = old_path.read_bytes()
    result = publish_dataset(context, contract, [corrected], "daily")

    assert old_path.read_bytes() == old_bytes
    path = context.artifact_path(result.manifest["artifact_paths"][0])
    assert pq.ParquetFile(path).read().to_pylist()[0]["data_cutoff_at"].endswith(
        "12:00:00Z"
    )
    assert path != old_path
