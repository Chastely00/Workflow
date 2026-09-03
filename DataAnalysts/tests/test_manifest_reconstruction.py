import json

import pyarrow as pa
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


def row(date: str, **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "date": date,
        "ticker": "2330",
        "source_available_date": date,
        "data_cutoff_at": f"{date}T10:00:00Z",
    }
    result.update(overrides)
    return result


def test_reconstruction_includes_untouched_partitions(context, contract):
    publish_dataset(context, contract, [row("2025-12-31")], run_scope="full_history")
    result = publish_dataset(context, contract, [row("2026-07-08")], run_scope="daily")

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert len(manifest["artifact_paths"]) == 2
    assert all("/versions/" in path for path in manifest["artifact_paths"])
    assert {path.split("/")[-2] for path in manifest["artifact_paths"]} == {
        "year=2025", "year=2026"
    }
    assert manifest["row_count"] == 2
    assert manifest["availability_date_range"] == ["2025-12-31", "2026-07-08"]
    assert manifest["data_cutoff_at"] == "2026-07-08T10:00:00Z"
    assert manifest["duplicate_count"] == 0
    assert len(manifest["schema_fingerprint"]) == 64


def test_reconstruction_rejects_schema_mismatch_without_touching_manifest(context, contract):
    publish_dataset(context, contract, [row("2025-12-31")], run_scope="full_history")
    manifest_path = context.store_path("manifests", "daily_tradability.json")
    second = publish_dataset(
        context,
        contract,
        [row("2025-12-31"), row("2026-07-08")],
        run_scope="full_history",
    )
    mismatched_path = context.artifact_path(
        next(path for path in second.manifest["artifact_paths"] if "year=2026" in path)
    )
    before = manifest_path.read_bytes()
    pq.write_table(
        pa.table(
            {
                "date": [20260708],
                "ticker": [2330],
                "source_available_date": [20260708],
                "data_cutoff_at": [20260708100000],
            }
        ),
        mismatched_path,
    )

    with pytest.raises(ArtifactError, match="schema mismatch"):
        reconstruct_manifest(context, contract)

    assert manifest_path.read_bytes() == before


def test_reconstruction_blocks_orphan_parquet(context, contract):
    publish_dataset(context, contract, [row("2026-07-08")], run_scope="full_history")
    orphan = context.artifact_path(f"{contract.base_path}/orphan.parquet")
    pq.write_table(pa.table({"unexpected": [1]}), orphan)

    with pytest.raises(ArtifactError, match="orphan parquet"):
        reconstruct_manifest(context, contract)


def test_reconstruction_collects_sorted_unique_source_collections(context, contract):
    result = publish_dataset(
        context,
        contract,
        [
            row("2025-12-31", source_collection="TEJ.ZETA"),
            row("2026-07-08", source_collection="TEJ.ALPHA"),
            row("2026-07-09", source_collection="TEJ.ZETA", ticker="2317"),
        ],
        run_scope="full_history",
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert manifest["source_collections"] == ["TEJ.ALPHA", "TEJ.ZETA"]


def test_reconstruction_streams_bounded_batches_without_whole_table_read(
    context, contract, monkeypatch
):
    publish_dataset(
        context,
        contract,
        [row("2025-12-31"), row("2026-07-08")],
        run_scope="full_history",
    )
    monkeypatch.setattr(
        pq.ParquetFile,
        "read",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("whole table read")),
    )

    manifest = reconstruct_manifest(context, contract)

    assert manifest["row_count"] == 2
