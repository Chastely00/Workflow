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
def tradability_contract() -> ArtifactContract:
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


def tradability_row(date: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "date": date,
        "ticker": "2330",
        "source_available_date": date,
        "data_cutoff_at": f"{date}T10:00:00Z",
    }
    row.update(overrides)
    return row


def _rows(context: DataAnalystsContext, contract: ArtifactContract, year: str):
    return pq.read_table(_active_path(context, contract, year)).to_pylist()


def _active_path(context: DataAnalystsContext, contract: ArtifactContract, year: str):
    manifest = json.loads(
        context.store_path("manifests", contract.manifest_file_name).read_text(
            encoding="utf-8"
        )
    )
    suffix = f"/{contract.partition_name}={year}/{contract.file_name}"
    matches = [path for path in manifest["artifact_paths"] if path.endswith(suffix)]
    assert len(matches) == 1
    return context.artifact_path(matches[0])


def test_daily_upsert_preserves_same_year_history(context, tradability_contract):
    publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2026-01-02"), tradability_row("2026-07-07")],
        run_scope="full_history",
    )

    result = publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2026-07-08")],
        run_scope="daily",
    )

    assert {row["date"] for row in _rows(context, tradability_contract, "2026")} == {
        "2026-01-02",
        "2026-07-07",
        "2026-07-08",
    }
    assert result.total_row_count == 3
    assert result.date_range == ("2026-01-02", "2026-07-08")


def test_full_history_replaces_complete_partition_inventory(context, tradability_contract):
    publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2025-12-31"), tradability_row("2026-01-02")],
        run_scope="full_history",
    )

    result = publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2026-07-08")],
        run_scope="full_history",
    )

    assert not any(path.endswith("/year=2025/part.parquet") for path in result.manifest["artifact_paths"])
    rows = _rows(context, tradability_contract, "2026")
    assert len(rows) == 1
    expected = tradability_row("2026-07-08")
    assert {key: rows[0][key] for key in expected} == expected
    assert result.manifest["artifact_paths"] == [
        result.manifest["artifact_paths"][0]
    ]
    assert "/versions/" in result.manifest["artifact_paths"][0]


def test_full_history_unifies_optional_columns_across_year_partitions(
    context, tradability_contract
):
    result = publish_dataset(
        context,
        tradability_contract,
        [
            tradability_row("2025-12-31", legacy_optional="old"),
            tradability_row("2026-01-02", current_optional="new"),
        ],
        run_scope="full_history",
    )

    schemas = [
        pq.ParquetFile(context.artifact_path(path)).schema_arrow
        for path in result.manifest["artifact_paths"]
    ]
    assert schemas[0].equals(schemas[1], check_metadata=False)
    assert {field.name for field in schemas[0]} >= {
        "legacy_optional",
        "current_optional",
    }


def test_partition_publication_switches_one_versioned_manifest_without_mixed_reader_view(
    context, tradability_contract
):
    first = publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2025-12-31"), tradability_row("2026-01-02")],
        run_scope="full_history",
    )
    old_paths = list(first.manifest["artifact_paths"])
    old_bytes = {path: context.artifact_path(path).read_bytes() for path in old_paths}

    second = publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2026-07-08")],
        run_scope="full_history",
    )

    assert second.manifest["artifact_paths"] != old_paths
    assert all("/versions/" in path for path in second.manifest["artifact_paths"])
    assert all(context.artifact_path(path).read_bytes() == payload for path, payload in old_bytes.items())
    assert all(context.artifact_path(path).is_file() for path in old_paths)


def test_bounded_update_copy_on_writes_complete_active_version(
    context, tradability_contract
):
    first = publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2025-12-31"), tradability_row("2026-01-02")],
        "full_history",
    )
    second = publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2026-07-08")],
        "daily",
    )
    assert set(first.manifest["artifact_paths"]).isdisjoint(second.manifest["artifact_paths"])
    assert len(second.manifest["artifact_paths"]) == 2
    assert all("/versions/" in path for path in second.manifest["artifact_paths"])


def test_empty_full_history_is_contract_controlled_and_switches_atomically(
    context, tradability_contract
):
    from dataclasses import replace

    allowed = replace(tradability_contract, allow_empty=True)
    first = publish_dataset(context, allowed, [tradability_row("2026-01-02")], "full_history")
    old_path = first.manifest["artifact_paths"][0]
    empty = publish_dataset(context, allowed, [], "full_history")
    assert empty.manifest["artifact_paths"] == []
    assert empty.manifest["row_count"] == 0
    assert context.artifact_path(old_path).is_file()

    required_context = DataAnalystsContext.from_paths(context.project_root / "required")
    original = publish_dataset(
        required_context, tradability_contract, [tradability_row("2026-01-02")], "full_history"
    )
    before = original.manifest_path.read_bytes()
    with pytest.raises(ArtifactError, match="does not allow empty"):
        publish_dataset(required_context, tradability_contract, [], "full_history")
    assert original.manifest_path.read_bytes() == before


def test_full_history_removal_rolls_back_when_manifest_write_fails(
    context, tradability_contract, monkeypatch
):
    publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2025-12-31"), tradability_row("2026-01-02")],
        run_scope="full_history",
    )
    old = _active_path(context, tradability_contract, "2025")
    old_bytes = old.read_bytes()

    monkeypatch.setattr(
        "data_analysts.dataset_publication.atomic_write_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("manifest fail")),
    )
    with pytest.raises(RuntimeError, match="manifest fail"):
        publish_dataset(
            context,
            tradability_contract,
            [tradability_row("2026-07-08")],
            run_scope="full_history",
        )

    assert old.read_bytes() == old_bytes


def test_upsert_replaces_corrected_logical_key(context, tradability_contract):
    original = tradability_row("2026-07-08", data_cutoff_at="2026-07-08T10:00:00Z")
    corrected = tradability_row("2026-07-08", data_cutoff_at="2026-07-08T12:00:00Z")
    publish_dataset(context, tradability_contract, [original], run_scope="full_history")

    publish_dataset(context, tradability_contract, [corrected], run_scope="daily")

    rows = _rows(context, tradability_contract, "2026")
    assert len(rows) == 1
    assert rows[0]["date"] == corrected["date"]
    assert rows[0]["ticker"] == corrected["ticker"]
    assert rows[0]["data_cutoff_at"] == corrected["data_cutoff_at"]


def test_idempotent_rerun_keeps_one_row(context, tradability_contract):
    row = tradability_row("2026-07-08")
    publish_dataset(context, tradability_contract, [row], run_scope="daily")
    first_bytes = _active_path(context, tradability_contract, "2026").read_bytes()

    result = publish_dataset(context, tradability_contract, [row], run_scope="daily")

    rows = _rows(context, tradability_contract, "2026")
    assert len(rows) == 1
    assert rows[0]["date"] == row["date"]
    assert rows[0]["ticker"] == row["ticker"]
    assert result.total_row_count == 1
    assert _active_path(context, tradability_contract, "2026").read_bytes() == first_bytes


def test_two_year_publish_writes_sorted_partitions_and_complete_manifest(
    context, tradability_contract
):
    result = publish_dataset(
        context,
        tradability_contract,
        [
            tradability_row("2026-07-08", ticker="2454"),
            tradability_row("2025-12-31"),
            tradability_row("2026-01-02", ticker="1101"),
        ],
        run_scope="full_history",
    )

    assert [row["date"] for row in _rows(context, tradability_contract, "2026")] == [
        "2026-01-02",
        "2026-07-08",
    ]
    assert len(result.touched_paths) == 2
    assert all("/versions/" in path for path in result.touched_paths)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_paths"] == list(result.touched_paths)
    assert manifest["row_count"] == 3
    assert manifest["date_range"] == ["2025-12-31", "2026-07-08"]


def test_incoming_duplicate_logical_keys_fail_closed(context, tradability_contract):
    row = tradability_row("2026-07-08")

    with pytest.raises(ArtifactError, match="duplicate logical key"):
        publish_dataset(context, tradability_contract, [row, dict(row)], run_scope="daily")


def test_cross_partition_incoming_duplicate_fails_before_any_write(
    context, tradability_contract
):
    ticker_key_contract = replace(tradability_contract, logical_key=("ticker",))

    with pytest.raises(ArtifactError, match="duplicate logical key"):
        publish_dataset(
            context,
            ticker_key_contract,
            [tradability_row("2025-12-31"), tradability_row("2026-01-02")],
            run_scope="full_history",
        )

    assert not context.artifact_path(ticker_key_contract.base_path).exists()
    assert not context.store_path("manifests", "daily_tradability.json").exists()


def test_global_preflight_wraps_missing_logical_key_field(
    context, tradability_contract
):
    incoming = tradability_row("2026-07-08")
    del incoming["ticker"]

    with pytest.raises(ArtifactError, match="daily_tradability.*ticker"):
        publish_dataset(
            context,
            tradability_contract,
            [incoming],
            run_scope="daily",
        )


def test_global_preflight_wraps_unhashable_logical_key(
    context, tradability_contract
):
    incoming = tradability_row("2026-07-08", ticker=["2330"])

    with pytest.raises(
        ArtifactError, match="daily_tradability.*unhashable logical key"
    ):
        publish_dataset(
            context,
            tradability_contract,
            [incoming],
            run_scope="daily",
        )


def test_existing_duplicate_logical_keys_fail_closed(context, tradability_contract):
    duplicate = tradability_row("2026-07-07")
    partition_path = context.artifact_path(
        tradability_contract.path_for_partition("2026")
    )
    partition_path.parent.mkdir(parents=True)
    from data_analysts.artifacts import ArtifactPublisher

    ArtifactPublisher(context).publish_parquet(
        str(partition_path.relative_to(context.data_store)).replace("\\", "/"),
        rows=[duplicate, dict(duplicate)],
        required_columns=list(tradability_contract.required_columns),
    )

    with pytest.raises(ArtifactError, match="duplicate logical key"):
        publish_dataset(
            context,
            tradability_contract,
            [tradability_row("2026-07-08")],
            run_scope="daily",
        )


def test_staging_failure_preserves_partition_and_manifest_bytes(
    context, tradability_contract, monkeypatch
):
    publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2026-07-07")],
        run_scope="full_history",
    )
    partition_path = _active_path(context, tradability_contract, "2026")
    manifest_path = context.store_path("manifests", "daily_tradability.json")
    before_partition = partition_path.read_bytes()
    before_manifest = manifest_path.read_bytes()

    def fail_write(*args, **kwargs):
        raise OSError("simulated staging failure")

    monkeypatch.setattr(
        "data_analysts.dataset_publication.pq.ParquetWriter.write_table", fail_write
    )
    with pytest.raises(OSError, match="simulated staging failure"):
        publish_dataset(
            context,
            tradability_contract,
            [tradability_row("2026-07-08")],
            run_scope="daily",
        )

    assert partition_path.read_bytes() == before_partition
    assert manifest_path.read_bytes() == before_manifest


def test_reconstruction_exception_restores_single_partition_and_manifest(
    context, tradability_contract, monkeypatch
):
    publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2026-07-07")],
        run_scope="full_history",
    )
    partition_path = _active_path(context, tradability_contract, "2026")
    manifest_path = context.store_path("manifests", "daily_tradability.json")
    before_partition = partition_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    from data_analysts import dataset_publication

    def validate_then_fail(*args, **kwargs):
        raise RuntimeError("simulated reconstruction failure")

    monkeypatch.setattr(dataset_publication, "validate_staged_dataset", validate_then_fail)
    with pytest.raises(RuntimeError, match="simulated reconstruction failure"):
        publish_dataset(
            context,
            tradability_contract,
            [tradability_row("2026-07-08")],
            run_scope="daily",
        )

    assert partition_path.read_bytes() == before_partition
    assert manifest_path.read_bytes() == before_manifest


def test_manifest_write_failure_rolls_back_multiple_partitions_and_new_file(
    context, tradability_contract, monkeypatch
):
    publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2025-12-31"), tradability_row("2026-01-02")],
        run_scope="full_history",
    )
    old_paths = [_active_path(context, tradability_contract, year) for year in ("2025", "2026")]
    before_partitions = {path: path.read_bytes() for path in old_paths}
    manifest_path = context.store_path("manifests", "daily_tradability.json")
    before_manifest = manifest_path.read_bytes()

    def fail_manifest_write(*args, **kwargs):
        raise RuntimeError("simulated manifest write failure")

    monkeypatch.setattr(
        "data_analysts.dataset_publication.atomic_write_text", fail_manifest_write
    )
    with pytest.raises(RuntimeError, match="simulated manifest write failure"):
        publish_dataset(
            context,
            tradability_contract,
            [
                tradability_row("2025-12-31", data_cutoff_at="2025-12-31T12:00:00Z"),
                tradability_row("2026-01-02", data_cutoff_at="2026-01-02T12:00:00Z"),
                tradability_row("2027-01-04"),
            ],
            run_scope="bounded_backfill",
        )

    assert {path: path.read_bytes() for path in old_paths} == before_partitions
    assert not any("year=2027" in path for path in json.loads(manifest_path.read_text(encoding="utf-8"))["artifact_paths"])
    assert manifest_path.read_bytes() == before_manifest


def test_manifest_switch_failure_keeps_old_immutable_version_and_manifest(
    context, tradability_contract, monkeypatch
):
    publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2026-07-07")],
        run_scope="full_history",
    )
    partition_path = _active_path(context, tradability_contract, "2026")
    original_bytes = partition_path.read_bytes()
    manifest_path = context.store_path("manifests", tradability_contract.manifest_file_name)
    before_manifest = manifest_path.read_bytes()
    from data_analysts import dataset_publication
    monkeypatch.setattr(
        dataset_publication, "atomic_write_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("manifest switch failure")),
    )
    with pytest.raises(RuntimeError, match="manifest switch failure"):
        publish_dataset(
            context,
            tradability_contract,
            [tradability_row("2026-07-08")],
            run_scope="daily",
        )

    assert partition_path.read_bytes() == original_bytes
    assert manifest_path.read_bytes() == before_manifest


def test_backup_creation_failure_cleans_staging_and_preserves_old_bytes(
    context, tradability_contract, monkeypatch
):
    publish_dataset(
        context,
        tradability_contract,
        [tradability_row("2026-07-07")],
        run_scope="full_history",
    )
    partition_path = _active_path(context, tradability_contract, "2026")
    original_bytes = partition_path.read_bytes()

    monkeypatch.setattr(
        "data_analysts.dataset_publication.os.link",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("link denied")),
    )
    monkeypatch.setattr(
        "data_analysts.dataset_publication.shutil.copy2",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("simulated copy failure")),
    )
    with pytest.raises(OSError, match="simulated copy failure"):
        publish_dataset(
            context,
            tradability_contract,
            [tradability_row("2027-07-08")],
            run_scope="daily",
        )

    assert partition_path.read_bytes() == original_bytes
