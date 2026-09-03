from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import data_analysts.filesystem as filesystem_module
import data_analysts.partition_transactions as transactions
from data_analysts.partition_transactions import (
    PartitionSpec,
    PublishTransactionError,
    commit_publish_transaction,
    stage_partition_rows,
)
from data_analysts.paths import DataAnalystsContext


SAMPLE_SPEC = PartitionSpec(
    base_path="canonical/derived/sample",
    partition_field="date",
    partition_name="year",
    key_fields=("date", "ticker"),
    required_columns=("date", "ticker", "value"),
)


def _row(date: str, ticker: str, value: int) -> dict[str, object]:
    return {"date": date, "ticker": ticker, "value": value}


def _artifact_path(year: str) -> str:
    return f"canonical/derived/sample/year={year}/part.parquet"


def _write_parquet(context: DataAnalystsContext, path: str, rows) -> Path:
    target = context.artifact_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(list(rows)), target)
    return target


def _read_rows(path: Path) -> list[dict[str, object]]:
    parquet_file = pq.ParquetFile(path)
    try:
        return parquet_file.read().to_pylist()
    finally:
        parquet_file.close()


def _staging_root(path: Path) -> Path:
    for candidate in path.parents:
        if candidate.parent.name == ".publish-staging":
            return candidate
    raise AssertionError(f"not under publish staging: {path}")


def _hashes(paths: list[Path]) -> dict[str, str]:
    return {
        path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.exists()
    }


def test_upsert_preserves_unaffected_rows_and_conserves_row_count(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _write_parquet(
        context,
        _artifact_path("2026"),
        [_row("2026-01-02", "2330", 10), _row("2026-01-03", "2330", 11)],
    )

    staged = stage_partition_rows(
        context,
        [_row("2026-01-03", "2330", 12), _row("2026-01-04", "2317", 20)],
        SAMPLE_SPEC,
        mode="upsert",
    )

    rows = _read_rows(staged[0].staged_path)
    assert [(row["date"], row["ticker"]) for row in rows] == [
        ("2026-01-02", "2330"),
        ("2026-01-03", "2330"),
        ("2026-01-04", "2317"),
    ]
    assert rows[1]["value"] == 12
    assert staged[0].row_count == 3
    assert staged[0].date_range == ("2026-01-02", "2026-01-04")


def test_upsert_reads_only_touched_partitions(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    for year in ("2024", "2025", "2026"):
        _write_parquet(
            context,
            _artifact_path(year),
            [_row(f"{year}-01-02", "2330", int(year))],
        )
    real_parquet_file = transactions.pq.ParquetFile
    formal_reads: list[Path] = []

    def recording_parquet_file(path, *args, **kwargs):
        resolved = Path(path).resolve()
        if resolved.is_relative_to(context.data_store / "canonical"):
            formal_reads.append(resolved)
        return real_parquet_file(path, *args, **kwargs)

    monkeypatch.setattr(transactions.pq, "ParquetFile", recording_parquet_file)

    staged = stage_partition_rows(
        context,
        [_row("2026-06-01", "2330", 26), _row("2025-06-01", "2330", 25)],
        SAMPLE_SPEC,
        mode="upsert",
    )

    assert {path.parent.name for path in formal_reads} == {"year=2025", "year=2026"}
    assert [item.artifact_path for item in staged] == [
        _artifact_path("2025"),
        _artifact_path("2026"),
    ]


def test_incoming_duplicate_keys_fail_closed_before_any_partition_read(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    _write_parquet(context, _artifact_path("2026"), [_row("2026-01-02", "2330", 1)])
    reads: list[Path] = []

    def unexpected_read(path, *args, **kwargs):
        reads.append(Path(path))
        raise AssertionError("incoming duplicates must fail before parquet reads")

    monkeypatch.setattr(transactions.pq, "ParquetFile", unexpected_read)

    with pytest.raises(PublishTransactionError, match="duplicate incoming key"):
        stage_partition_rows(
            context,
            [_row("2026-01-03", "2330", 2), _row("2026-01-03", "2330", 3)],
            SAMPLE_SPEC,
            mode="upsert",
        )

    assert reads == []
    assert not context.store_path("jobs", ".publish-staging").exists()


def test_existing_duplicate_keys_fail_closed(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    _write_parquet(
        context,
        _artifact_path("2026"),
        [_row("2026-01-02", "2330", 1), _row("2026-01-02", "2330", 2)],
    )

    with pytest.raises(PublishTransactionError, match="duplicate existing key"):
        stage_partition_rows(
            context,
            [_row("2026-01-03", "2330", 3)],
            SAMPLE_SPEC,
            mode="upsert",
        )

    assert not context.store_path("jobs", ".publish-staging").exists()


def test_staged_rows_are_sorted_by_configured_keys(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    staged = stage_partition_rows(
        context,
        [
            _row("2026-01-03", "2330", 3),
            _row("2026-01-02", "2454", 2),
            _row("2026-01-02", "2317", 1),
        ],
        SAMPLE_SPEC,
        mode="replace",
    )

    assert [
        (row["date"], row["ticker"])
        for row in _read_rows(staged[0].staged_path)
    ] == [
        ("2026-01-02", "2317"),
        ("2026-01-02", "2454"),
        ("2026-01-03", "2330"),
    ]


def test_partition_spec_is_generic_over_paths_fields_and_columns(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/custom_actions",
        partition_field="event_date",
        partition_name="event_year",
        key_fields=("event_date", "event_id"),
        required_columns=("event_date", "event_id", "payload"),
    )

    staged = stage_partition_rows(
        context,
        [
            {"event_date": "2031-02-02", "event_id": "b", "payload": "second"},
            {"event_date": "2031-02-01", "event_id": "a", "payload": "first"},
        ],
        spec,
        mode="replace",
    )

    assert staged[0].artifact_path == (
        "canonical/events/custom_actions/event_year=2031/part.parquet"
    )
    assert [row["event_id"] for row in _read_rows(staged[0].staged_path)] == ["a", "b"]


def test_partition_spec_supports_identity_derivation_for_non_date_values(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/reference/venues",
        partition_field="venue",
        partition_name="venue",
        key_fields=("venue", "ticker"),
        required_columns=("venue", "ticker", "value"),
        partition_derivation="identity",
    )

    staged = stage_partition_rows(
        context,
        [
            {"venue": "NASDAQ", "ticker": "MSFT", "value": 1},
            {"venue": "NASD", "ticker": "TEST", "value": 2},
        ],
        spec,
        mode="replace",
    )

    assert [item.artifact_path for item in staged] == [
        "canonical/reference/venues/venue=NASD/part.parquet",
        "canonical/reference/venues/venue=NASDAQ/part.parquet",
    ]


def test_upsert_canonicalizes_python_numpy_and_date_like_keys(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/scalars",
        partition_field="venue",
        partition_name="venue",
        key_fields=("event_date", "security_id"),
        required_columns=("venue", "event_date", "security_id", "value"),
        partition_derivation="identity",
    )
    artifact_path = "canonical/events/scalars/venue=TWSE/part.parquet"
    _write_parquet(
        context,
        artifact_path,
        [
            {
                "venue": "TWSE",
                "event_date": date(2026, 1, 2),
                "security_id": 1,
                "value": 10,
            }
        ],
    )

    staged = stage_partition_rows(
        context,
        [
            {
                "venue": "TWSE",
                "event_date": np.datetime64("2026-01-02"),
                "security_id": np.int64(1),
                "value": 11,
            }
        ],
        spec,
        mode="upsert",
    )

    assert staged[0].row_count == 1
    assert _read_rows(staged[0].staged_path)[0]["value"] == 11


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [
        ("Y", "2026", date(2026, 1, 1)),
        ("M", "2026-02", date(2026, 2, 1)),
        ("W", "2026-01-02", date(2026, 1, 1)),
        ("D", "2026-01-02", date(2026, 1, 2)),
        ("h", "2026-01-02T03", datetime(2026, 1, 2, 3)),
        ("m", "2026-01-02T03:04", datetime(2026, 1, 2, 3, 4)),
        ("s", "2026-01-02T03:04:05", datetime(2026, 1, 2, 3, 4, 5)),
        (
            "ms",
            "2026-01-02T03:04:05.123",
            datetime(2026, 1, 2, 3, 4, 5, 123000),
        ),
        (
            "us",
            "2026-01-02T03:04:05.123456",
            datetime(2026, 1, 2, 3, 4, 5, 123456),
        ),
        (
            "ns",
            "2026-01-02T03:04:05.123456000",
            datetime(2026, 1, 2, 3, 4, 5, 123456),
        ),
    ],
)
def test_numpy_datetime64_units_preserve_partition_schema_and_value(
    tmp_path, unit, value, expected
):
    context = DataAnalystsContext.from_paths(tmp_path)

    staged = stage_partition_rows(
        context,
        [{"date": np.datetime64(value, unit), "ticker": "2330", "value": 1}],
        SAMPLE_SPEC,
        mode="replace",
    )

    assert staged[0].artifact_path == _artifact_path("2026")
    parquet_file = pq.ParquetFile(staged[0].staged_path)
    try:
        date_type = parquet_file.schema_arrow.field("date").type
        rows = parquet_file.read().to_pylist()
    finally:
        parquet_file.close()
    if unit in {"Y", "M", "W", "D"}:
        assert pa.types.is_date(date_type)
    else:
        assert pa.types.is_timestamp(date_type)
    assert rows == [{"date": expected, "ticker": "2330", "value": 1}]


@pytest.mark.parametrize(
    ("python_value", "numpy_value"),
    [
        (date(2026, 1, 2), np.datetime64("2026-01-02", "D")),
        (
            datetime(2026, 1, 2, 3, 4, 5),
            np.datetime64("2026-01-02T03:04:05", "s"),
        ),
        (
            datetime(2026, 1, 2, 3, 4, 5, 123456),
            np.datetime64("2026-01-02T03:04:05.123456000", "ns"),
        ),
    ],
)
def test_equivalent_python_and_numpy_date_like_keys_are_duplicates(
    tmp_path, python_value, numpy_value
):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PublishTransactionError, match="duplicate incoming key"):
        stage_partition_rows(
            context,
            [
                {"date": python_value, "ticker": "2330", "value": 1},
                {"date": numpy_value, "ticker": "2330", "value": 2},
            ],
            SAMPLE_SPEC,
            mode="replace",
        )

    assert not context.store_path("jobs", ".publish-staging").exists()


def test_numpy_datetime64_ns_upsert_matches_python_datetime_key(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    target = _write_parquet(
        context,
        _artifact_path("2026"),
        [
            {
                "date": datetime(2026, 1, 2, 3, 4, 5, 123456),
                "ticker": "2330",
                "value": 1,
            }
        ],
    )

    staged = stage_partition_rows(
        context,
        [
            {
                "date": np.datetime64("2026-01-02T03:04:05.123456000", "ns"),
                "ticker": "2330",
                "value": 2,
            }
        ],
        SAMPLE_SPEC,
        mode="upsert",
    )

    assert staged[0].artifact_path == _artifact_path("2026")
    assert staged[0].row_count == 1
    assert _read_rows(staged[0].staged_path) == [
        {
            "date": datetime(2026, 1, 2, 3, 4, 5, 123456),
            "ticker": "2330",
            "value": 2,
        }
    ]
    assert target.exists()


def test_numpy_datetime64_ns_preserves_sub_microsecond_value_across_upsert(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    value = np.datetime64("2026-01-02T03:04:05.123456789", "ns")

    first = stage_partition_rows(
        context,
        [{"date": value, "ticker": "2330", "value": 1}],
        SAMPLE_SPEC,
        mode="replace",
    )
    commit_publish_transaction(context, first, {})
    staged = stage_partition_rows(
        context,
        [{"date": value, "ticker": "2330", "value": 2}],
        SAMPLE_SPEC,
        mode="upsert",
    )

    parquet_file = pq.ParquetFile(staged[0].staged_path)
    try:
        date_type = parquet_file.schema_arrow.field("date").type
        rows = parquet_file.read().to_pylist()
    finally:
        parquet_file.close()
    assert staged[0].artifact_path == _artifact_path("2026")
    assert staged[0].row_count == 1
    assert date_type == pa.timestamp("ns")
    assert str(rows[0]["date"]) == "2026-01-02 03:04:05.123456789"
    assert rows[0]["value"] == 2


def test_unsupported_numpy_datetime64_unit_fails_before_staging_root(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PublishTransactionError, match="unsupported numpy datetime64 unit"):
        stage_partition_rows(
            context,
            [
                {
                    "date": np.datetime64("1970-01-01T00:00:00", "ps"),
                    "ticker": "2330",
                    "value": 1,
                }
            ],
            SAMPLE_SPEC,
            mode="replace",
        )

    assert not context.store_path("jobs", ".publish-staging").exists()


def test_non_key_unsupported_datetime_fails_before_staging_root(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/observations",
        partition_field="venue",
        partition_name="venue",
        key_fields=("venue", "event_id"),
        required_columns=("venue", "event_id", "observed_at"),
        partition_derivation="identity",
    )
    staging_base = context.store_path("jobs", ".publish-staging")
    real_mkdir = Path.mkdir
    staging_mkdir_calls: list[Path] = []

    def recording_mkdir(path, *args, **kwargs):
        candidate = Path(path)
        if staging_base in candidate.parents or candidate == staging_base:
            staging_mkdir_calls.append(candidate)
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", recording_mkdir)

    with pytest.raises(PublishTransactionError, match="unsupported numpy datetime64 unit"):
        stage_partition_rows(
            context,
            [
                {
                    "venue": "TWSE",
                    "event_id": "event-1",
                    "observed_at": np.datetime64("1970-01-01T00:00:00", "ps"),
                }
            ],
            spec,
            mode="replace",
        )

    assert staging_mkdir_calls == []
    assert not staging_base.exists()


@pytest.mark.parametrize(
    "unsupported_value",
    [
        pytest.param(1 + 2j, id="complex"),
        pytest.param(object(), id="object"),
        pytest.param(-(2**63) - 1, id="below-int64"),
        pytest.param(2**63, id="above-int64"),
    ],
)
def test_required_non_key_arrow_unsupported_value_fails_before_staging_root(
    tmp_path, monkeypatch, unsupported_value
):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/observations",
        partition_field="venue",
        partition_name="venue",
        key_fields=("venue", "event_id"),
        required_columns=("venue", "event_id", "payload"),
        partition_derivation="identity",
    )
    formal_target = _write_parquet(
        context,
        "canonical/events/observations/venue=TWSE/part.parquet",
        [{"venue": "TWSE", "event_id": "event-1", "payload": 7}],
    )
    before_bytes = formal_target.read_bytes()
    before_hash = _hashes([formal_target])
    staging_base = context.store_path("jobs", ".publish-staging")
    real_mkdir = Path.mkdir
    staging_mkdir_calls: list[Path] = []

    def recording_mkdir(path, *args, **kwargs):
        candidate = Path(path)
        if staging_base in candidate.parents or candidate == staging_base:
            staging_mkdir_calls.append(candidate)
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", recording_mkdir)

    with pytest.raises(PublishTransactionError, match="Arrow materialization"):
        stage_partition_rows(
            context,
            [
                {
                    "venue": "TWSE",
                    "event_id": "event-1",
                    "payload": unsupported_value,
                }
            ],
            spec,
            mode="replace",
        )

    assert formal_target.read_bytes() == before_bytes
    assert _hashes([formal_target]) == before_hash
    assert staging_mkdir_calls == []
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_base.exists()


@pytest.mark.parametrize("boundary_value", [-(2**63), (2**63) - 1])
def test_required_non_key_arrow_integer_boundaries_are_materializable(
    tmp_path, boundary_value
):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/integer_boundaries",
        partition_field="venue",
        partition_name="venue",
        key_fields=("venue", "event_id"),
        required_columns=("venue", "event_id", "payload"),
        partition_derivation="identity",
    )

    staged = stage_partition_rows(
        context,
        [{"venue": "TWSE", "event_id": "event-1", "payload": boundary_value}],
        spec,
        mode="replace",
    )

    assert _read_rows(staged[0].staged_path)[0]["payload"] == boundary_value


def test_out_of_range_numpy_date_fails_before_staging_root(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PublishTransactionError, match="safe datetime range"):
        stage_partition_rows(
            context,
            [
                {
                    "date": np.datetime64("12000-01-02", "D"),
                    "ticker": "2330",
                    "value": 1,
                }
            ],
            SAMPLE_SPEC,
            mode="replace",
        )

    assert not context.store_path("jobs", ".publish-staging").exists()


def test_year_partition_rejects_non_date_like_scalar(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PublishTransactionError, match="date/datetime-like"):
        stage_partition_rows(
            context,
            [{"date": 20260102, "ticker": "2330", "value": 1}],
            SAMPLE_SPEC,
            mode="replace",
        )

    assert not context.store_path("jobs", ".publish-staging").exists()


def test_incoming_signed_zero_keys_fail_as_duplicates(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/numeric_keys",
        partition_field="venue",
        partition_name="venue",
        key_fields=("venue", "numeric_key"),
        required_columns=("venue", "numeric_key", "value"),
        partition_derivation="identity",
    )

    with pytest.raises(PublishTransactionError, match="duplicate incoming key"):
        stage_partition_rows(
            context,
            [
                {"venue": "TWSE", "numeric_key": -0.0, "value": 10},
                {"venue": "TWSE", "numeric_key": 0.0, "value": 11},
            ],
            spec,
            mode="replace",
        )


def test_upsert_signed_zero_keys_replace_the_same_row(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/numeric_keys",
        partition_field="venue",
        partition_name="venue",
        key_fields=("venue", "numeric_key"),
        required_columns=("venue", "numeric_key", "value"),
        partition_derivation="identity",
    )
    artifact_path = "canonical/events/numeric_keys/venue=TWSE/part.parquet"
    _write_parquet(
        context,
        artifact_path,
        [{"venue": "TWSE", "numeric_key": -0.0, "value": 10}],
    )

    staged = stage_partition_rows(
        context,
        [{"venue": "TWSE", "numeric_key": 0.0, "value": 11}],
        spec,
        mode="upsert",
    )

    rows = _read_rows(staged[0].staged_path)
    assert staged[0].row_count == 1
    assert rows == [{"venue": "TWSE", "numeric_key": 0.0, "value": 11}]


def test_identity_partition_canonicalizes_signed_zero_across_transactions(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/numeric_partitions",
        partition_field="bucket",
        partition_name="bucket",
        key_fields=("bucket", "event_id"),
        required_columns=("bucket", "event_id", "value"),
        partition_derivation="identity",
    )

    first = stage_partition_rows(
        context,
        [{"bucket": -0.0, "event_id": "event-1", "value": 10}],
        spec,
        mode="replace",
    )
    commit_publish_transaction(context, first, {})
    second = stage_partition_rows(
        context,
        [{"bucket": 0.0, "event_id": "event-1", "value": 11}],
        spec,
        mode="upsert",
    )
    commit_publish_transaction(context, second, {})

    formal_paths = list(
        context.store_path("canonical", "events", "numeric_partitions").glob(
            "bucket=*/part.parquet"
        )
    )
    assert [path.parent.name for path in formal_paths] == ["bucket=0.0"]
    assert _read_rows(formal_paths[0]) == [
        {"bucket": 0.0, "event_id": "event-1", "value": 11}
    ]


@pytest.mark.parametrize(
    "invalid_value",
    [
        "A.",
        "A ",
        "A:stream",
        "CON",
        "A?",
        "A*",
        'A"',
        "A<",
        "A>",
        "A|",
        "A\x00",
        "A\x01",
        "A\x1f",
        "A\x7f",
    ],
)
def test_identity_partition_rejects_windows_unsafe_value_without_mutation(
    tmp_path, monkeypatch, invalid_value
):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/path_values",
        partition_field="bucket",
        partition_name="bucket",
        key_fields=("bucket", "event_id"),
        required_columns=("bucket", "event_id", "value"),
        partition_derivation="identity",
    )
    formal_target = _write_parquet(
        context,
        "canonical/events/path_values/bucket=A/part.parquet",
        [{"bucket": "A", "event_id": "event-1", "value": 1}],
    )
    before_bytes = formal_target.read_bytes()
    before_hash = _hashes([formal_target])
    staging_base = context.store_path("jobs", ".publish-staging")
    real_mkdir = Path.mkdir
    staging_mkdir_calls: list[Path] = []

    def recording_mkdir(path, *args, **kwargs):
        candidate = Path(path)
        if staging_base in candidate.parents or candidate == staging_base:
            staging_mkdir_calls.append(candidate)
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", recording_mkdir)

    with pytest.raises(PublishTransactionError, match="invalid Windows component"):
        stage_partition_rows(
            context,
            [{"bucket": invalid_value, "event_id": "event-1", "value": 2}],
            spec,
            mode="replace",
        )

    assert formal_target.read_bytes() == before_bytes
    assert _hashes([formal_target]) == before_hash
    assert staging_mkdir_calls == []
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_base.exists()


def test_identity_partitions_reject_case_aliases_in_one_stage_without_mutation(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/path_values",
        partition_field="bucket",
        partition_name="bucket",
        key_fields=("bucket", "event_id"),
        required_columns=("bucket", "event_id", "value"),
        partition_derivation="identity",
    )
    staging_base = context.store_path("jobs", ".publish-staging")
    real_mkdir = Path.mkdir
    staging_mkdir_calls: list[Path] = []

    def recording_mkdir(path, *args, **kwargs):
        candidate = Path(path)
        if staging_base in candidate.parents or candidate == staging_base:
            staging_mkdir_calls.append(candidate)
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", recording_mkdir)

    with pytest.raises(PublishTransactionError, match="case-insensitive publish target"):
        stage_partition_rows(
            context,
            [
                {"bucket": "A", "event_id": "event-1", "value": 1},
                {"bucket": "a", "event_id": "event-2", "value": 2},
            ],
            spec,
            mode="replace",
        )

    assert staging_mkdir_calls == []
    assert not staging_base.exists()
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not context.store_path("canonical", "events", "path_values").exists()


def test_identity_partition_rejects_cross_transaction_case_alias_bounded_and_clean(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/path_values",
        partition_field="bucket",
        partition_name="bucket",
        key_fields=("bucket", "event_id"),
        required_columns=("bucket", "event_id", "value"),
        partition_derivation="identity",
    )
    first = stage_partition_rows(
        context,
        [{"bucket": "A", "event_id": "event-1", "value": 1}],
        spec,
        mode="replace",
    )
    commit_publish_transaction(context, first, {})
    formal_target = context.artifact_path(
        "canonical/events/path_values/bucket=A/part.parquet"
    )
    before = formal_target.read_bytes()
    unrelated = context.store_path("canonical", "unrelated")
    unrelated.mkdir(parents=True)
    (unrelated / "sentinel.txt").write_text("untouched", encoding="utf-8")
    real_iterdir = Path.iterdir
    scanned_directories: list[Path] = []

    def recording_iterdir(path):
        scanned_directories.append(Path(path))
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", recording_iterdir)

    with pytest.raises(PublishTransactionError, match="case-insensitive publish target"):
        stage_partition_rows(
            context,
            [{"bucket": "a", "event_id": "event-1", "value": 2}],
            spec,
            mode="replace",
        )

    assert formal_target.read_bytes() == before
    assert unrelated not in scanned_directories
    assert set(scanned_directories) <= {
        context.data_store,
        context.store_path("canonical"),
        context.store_path("canonical", "events"),
        context.store_path("canonical", "events", "path_values"),
    }
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not context.store_path("jobs", ".publish-staging").exists()


def test_many_identity_partitions_enumerate_each_touched_parent_once_per_phase(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path="canonical/events/path_values",
        partition_field="bucket",
        partition_name="bucket",
        key_fields=("bucket", "event_id"),
        required_columns=("bucket", "event_id", "value"),
        partition_derivation="identity",
    )
    partition_count = 64
    bucket_parents = [
        context.store_path(
            "canonical", "events", "path_values", f"bucket={number:03d}"
        )
        for number in range(partition_count)
    ]
    for parent in bucket_parents:
        parent.mkdir(parents=True, exist_ok=True)

    real_iterdir = Path.iterdir
    enumeration_counts: dict[Path, int] = {}

    def recording_iterdir(path):
        resolved = Path(path).resolve()
        enumeration_counts[resolved] = enumeration_counts.get(resolved, 0) + 1
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", recording_iterdir)

    staged = stage_partition_rows(
        context,
        [
            {
                "bucket": f"{number:03d}",
                "event_id": f"event-{number:03d}",
                "value": number,
            }
            for number in range(partition_count)
        ],
        spec,
        mode="replace",
    )
    commit_publish_transaction(context, staged, {})

    expected_parents = {
        context.data_store.resolve(),
        context.store_path("canonical").resolve(),
        context.store_path("canonical", "events").resolve(),
        context.store_path("canonical", "events", "path_values").resolve(),
        *(parent.resolve() for parent in bucket_parents),
    }
    assert set(enumeration_counts) == expected_parents
    assert enumeration_counts == {parent: 2 for parent in expected_parents}
    assert sum(enumeration_counts.values()) == 2 * len(expected_parents)


@pytest.mark.parametrize(
    "base_path",
    [
        "canonical/base_alias.",
        "canonical/base_alias ",
        "canonical/base:stream",
        "canonical/CON",
        "jobs/publish.lock",
        "jobs/.publish-staging",
        "Runtime/canonical",
        "canonical/RUNS/sample",
        "canonical/Real_All_Products/sample",
    ],
)
def test_partition_base_rejects_windows_aliases_and_control_paths_before_staging(
    tmp_path, monkeypatch, base_path
):
    context = DataAnalystsContext.from_paths(tmp_path)
    sentinel = _write_parquet(
        context, _artifact_path("2026"), [_row("2026-01-02", "2330", 1)]
    )
    before_bytes = sentinel.read_bytes()
    before_hash = _hashes([sentinel])
    spec = replace(SAMPLE_SPEC, base_path=base_path)
    staging_base = context.store_path("jobs", ".publish-staging")
    real_mkdir = Path.mkdir
    staging_mkdir_calls: list[Path] = []

    def recording_mkdir(path, *args, **kwargs):
        candidate = Path(path)
        if staging_base in candidate.parents or candidate == staging_base:
            staging_mkdir_calls.append(candidate)
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", recording_mkdir)

    with pytest.raises(PublishTransactionError, match="formal artifact path"):
        stage_partition_rows(
            context,
            [_row("2026-01-02", "2330", 2)],
            spec,
            mode="replace",
        )

    assert sentinel.read_bytes() == before_bytes
    assert _hashes([sentinel]) == before_hash
    assert staging_mkdir_calls == []
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_base.exists()


@pytest.mark.parametrize("partition_name", ["year.", "year ", "year:stream", "CON"])
def test_partition_name_rejects_windows_unsafe_component_before_staging(
    tmp_path, partition_name
):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = replace(SAMPLE_SPEC, partition_name=partition_name)

    with pytest.raises(PublishTransactionError, match="invalid Windows component"):
        stage_partition_rows(
            context,
            [_row("2026-01-02", "2330", 1)],
            spec,
            mode="replace",
        )

    assert not context.store_path("jobs", "publish.lock").exists()
    assert not context.store_path("jobs", ".publish-staging").exists()


@pytest.mark.parametrize(
    "base_path",
    ["../escape", "canonical/../../escape", "runtime/canonical/sample"],
)
def test_stage_rejects_artifact_path_boundary_violations(tmp_path, base_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    spec = PartitionSpec(
        base_path=base_path,
        partition_field="date",
        partition_name="year",
        key_fields=("date", "ticker"),
        required_columns=("date", "ticker", "value"),
    )

    with pytest.raises(PublishTransactionError, match="artifact path"):
        stage_partition_rows(
            context,
            [_row("2026-01-02", "2330", 1)],
            spec,
            mode="replace",
        )


def test_stage_failure_cleans_every_staged_partition(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    real_write_table = transactions.pq.write_table
    calls = 0

    def fail_second_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected stage failure")
        return real_write_table(*args, **kwargs)

    monkeypatch.setattr(transactions.pq, "write_table", fail_second_write)

    with pytest.raises(PublishTransactionError, match="stage partition transaction"):
        stage_partition_rows(
            context,
            [_row("2025-01-02", "2330", 1), _row("2026-01-02", "2330", 2)],
            SAMPLE_SPEC,
            mode="replace",
        )

    assert not context.store_path("jobs", ".publish-staging").exists()


def test_commit_rejects_existing_lock_without_touching_formal_files(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    target = _write_parquet(
        context, _artifact_path("2026"), [_row("2026-01-02", "2330", 1)]
    )
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    before = target.read_bytes()
    staging_root = _staging_root(staged[0].staged_path)
    lock_path = context.store_path("jobs", "publish.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("another transaction", encoding="utf-8")

    with pytest.raises(PublishTransactionError, match="publish lock already exists"):
        commit_publish_transaction(context, staged, {})

    assert target.read_bytes() == before
    assert lock_path.read_text(encoding="utf-8") == "another transaction"
    assert not staging_root.exists()


def test_stale_upsert_fails_closed_in_real_interleaving(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    target = _write_parquet(
        context, _artifact_path("2026"), [_row("2026-01-01", "2330", 1)]
    )
    staged_a = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="upsert",
    )
    staged_b = stage_partition_rows(
        context,
        [_row("2026-01-03", "2330", 3)],
        SAMPLE_SPEC,
        mode="upsert",
    )

    commit_publish_transaction(context, staged_b, {})
    with pytest.raises(PublishTransactionError, match="stale upsert source"):
        commit_publish_transaction(context, staged_a, {})

    assert [(row["date"], row["value"]) for row in _read_rows(target)] == [
        ("2026-01-01", 1),
        ("2026-01-03", 3),
    ]
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not _staging_root(staged_a[0].staged_path).exists()


def test_upsert_rejects_target_appearing_after_absent_source_stage(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="upsert",
    )
    target = _write_parquet(
        context,
        _artifact_path("2026"),
        [_row("2026-01-01", "CONCURRENT", 99)],
    )
    concurrent_bytes = target.read_bytes()
    formal_replacements: list[Path] = []
    real_replace = filesystem_module.os.replace

    def record_formal_replacement(source, destination):
        destination_path = Path(destination)
        if destination_path == target:
            formal_replacements.append(destination_path)
        return real_replace(source, destination)

    monkeypatch.setattr(filesystem_module.os, "replace", record_formal_replacement)

    with pytest.raises(
        PublishTransactionError, match="stale upsert source existence"
    ):
        commit_publish_transaction(context, staged, {})

    assert target.read_bytes() == concurrent_bytes
    assert formal_replacements == []


def test_target_appearing_during_lock_acquisition_is_restored_on_failure(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    staged = stage_partition_rows(
        context,
        [_row("2025-01-02", "2330", 5), _row("2026-01-02", "2330", 6)],
        SAMPLE_SPEC,
        mode="replace",
    )
    concurrent_target = context.artifact_path(_artifact_path("2025"))
    lock_path = context.store_path("jobs", "publish.lock")
    real_path_open = Path.open
    appeared = False

    def create_target_before_lock(path, *args, **kwargs):
        nonlocal appeared
        if Path(path) == lock_path and args and args[0] == "x" and not appeared:
            appeared = True
            _write_parquet(
                context,
                _artifact_path("2025"),
                [_row("2025-01-01", "OTHER", 99)],
            )
        return real_path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", create_target_before_lock)
    real_replace = filesystem_module.os.replace
    partition_replaces = 0

    def fail_second_partition_replace(source, target):
        nonlocal partition_replaces
        if Path(target).suffix == ".parquet":
            partition_replaces += 1
            if partition_replaces == 2:
                raise OSError("injected commit failure")
        return real_replace(source, target)

    monkeypatch.setattr(filesystem_module.os, "replace", fail_second_partition_replace)

    with pytest.raises(PublishTransactionError, match="commit publish transaction"):
        commit_publish_transaction(context, staged, {})

    assert _read_rows(concurrent_target) == [_row("2025-01-01", "OTHER", 99)]


def test_commit_preflights_all_staged_files_before_formal_replacement(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    targets = [
        _write_parquet(context, _artifact_path(year), [_row(f"{year}-01-02", "2330", 1)])
        for year in ("2025", "2026")
    ]
    before = _hashes(targets)
    staged = stage_partition_rows(
        context,
        [_row("2025-01-02", "2330", 2), _row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_root = _staging_root(staged[0].staged_path)
    staged[1].staged_path.unlink()

    with pytest.raises(PublishTransactionError, match="staged partition"):
        commit_publish_transaction(context, staged, {})

    assert _hashes(targets) == before
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_root.exists()


def test_commit_rejects_mixed_transactions_and_cleans_both_staging_roots(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    first = stage_partition_rows(
        context,
        [_row("2025-01-02", "2330", 1)],
        SAMPLE_SPEC,
        mode="replace",
    )
    second = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_roots = {
        _staging_root(first[0].staged_path),
        _staging_root(second[0].staged_path),
    }

    with pytest.raises(PublishTransactionError, match="multiple transactions"):
        commit_publish_transaction(context, [*first, *second], {})

    assert all(not root.exists() for root in staging_roots)
    assert not context.store_path("jobs", "publish.lock").exists()


def test_metadata_staging_failure_happens_before_formal_replacement(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    target = _write_parquet(
        context, _artifact_path("2026"), [_row("2026-01-02", "2330", 1)]
    )
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    before = target.read_bytes()
    staging_root = _staging_root(staged[0].staged_path)

    with pytest.raises(PublishTransactionError, match="stage metadata"):
        commit_publish_transaction(
            context,
            staged,
            {"manifests/sample.json": {"not_json": object()}},
        )

    assert target.read_bytes() == before
    assert not context.store_path("manifests", "sample.json").exists()
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_root.exists()


def test_source_preconditions_are_rechecked_after_publish_lock(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    source_path = "canonical/derived/source/year=2026/part.parquet"
    source = _write_parquet(
        context, source_path, [_row("2026-01-02", "2330", 1)]
    )
    expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    metadata = context.store_path("metadata", "sentinel.json")
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_bytes(b'{"status":"old"}\n')
    before_metadata = metadata.read_bytes()
    lock_path = context.store_path("jobs", "publish.lock")
    real_path_open = Path.open
    mutated = False

    def mutate_source_after_lock(path, *args, **kwargs):
        nonlocal mutated
        opened = real_path_open(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        if Path(path) == lock_path and mode == "x" and not mutated:
            mutated = True
            source.write_bytes(b"changed after lock acquisition")
        return opened

    monkeypatch.setattr(Path, "open", mutate_source_after_lock)

    with pytest.raises(PublishTransactionError, match="source precondition hash"):
        commit_publish_transaction(
            context,
            [],
            {"metadata/sentinel.json": {"status": "new"}},
            source_preconditions={source_path: expected_sha256},
        )

    assert mutated is True
    assert metadata.read_bytes() == before_metadata
    assert not lock_path.exists()


def test_source_precondition_expected_absence_is_backward_compatible(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    commit_publish_transaction(
        context,
        [],
        {"metadata/sentinel.json": {"status": "ready"}},
        source_preconditions={"manifests/not_yet_published.json": None},
    )

    assert json.loads(
        context.store_path("metadata", "sentinel.json").read_text(encoding="utf-8")
    ) == {"status": "ready"}


@pytest.mark.parametrize(
    ("source_path", "expected_hash", "message"),
    [
        ("../outside.json", "0" * 64, "invalid source precondition artifact path"),
        ("jobs/publish.lock", "0" * 64, "reserved source precondition artifact path"),
        ("metadata/CON/report.json", "0" * 64, "reserved source precondition artifact path"),
        ("metadata/missing.json", "0" * 64, "source precondition missing"),
    ],
)
def test_source_preconditions_fail_closed_on_invalid_or_missing_paths(
    tmp_path, source_path, expected_hash, message
):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PublishTransactionError, match=message):
        commit_publish_transaction(
            context,
            [],
            {"metadata/sentinel.json": {"status": "new"}},
            source_preconditions={source_path: expected_hash},
        )

    assert not context.store_path("metadata", "sentinel.json").exists()


def test_source_preconditions_reject_case_collisions(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PublishTransactionError, match="case-insensitive publish target"):
        commit_publish_transaction(
            context,
            [],
            {},
            source_preconditions={
                "manifests/source.json": None,
                "MANIFESTS/SOURCE.JSON": None,
            },
        )


def test_source_precondition_rejects_case_alias_of_publish_target(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PublishTransactionError, match="case-insensitive publish target"):
        commit_publish_transaction(
            context,
            [],
            {"manifests/source.json": {"status": "ready"}},
            source_preconditions={"MANIFESTS/SOURCE.JSON": None},
        )

    assert not context.store_path("manifests", "source.json").exists()


def test_source_precondition_unreadable_file_fails_before_metadata_staging(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    source_path = "manifests/source.json"
    source = context.artifact_path(source_path)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'{}\n')
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    real_content_sha256 = transactions._content_sha256

    def unreadable_source(path):
        if Path(path) == source:
            raise PermissionError("injected unreadable source")
        return real_content_sha256(path)

    monkeypatch.setattr(transactions, "_content_sha256", unreadable_source)

    with pytest.raises(PublishTransactionError, match="source precondition unreadable"):
        commit_publish_transaction(
            context,
            [],
            {"metadata/sentinel.json": {"status": "new"}},
            source_preconditions={source_path: expected_hash},
        )

    assert not context.store_path("metadata", "sentinel.json").exists()


def test_source_precondition_expected_absence_fails_closed_when_stat_is_unreadable(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    source_path = "manifests/source.json"
    source = context.artifact_path(source_path)
    real_stat = Path.stat

    def unreadable_stat(path, *args, **kwargs):
        if Path(path) == source:
            raise PermissionError("injected unreadable source path")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unreadable_stat)

    with pytest.raises(PublishTransactionError, match="source precondition unreadable"):
        commit_publish_transaction(
            context,
            [],
            {"metadata/sentinel.json": {"status": "new"}},
            source_preconditions={source_path: None},
        )

    assert not context.store_path("metadata", "sentinel.json").exists()


def test_metadata_targets_must_remain_inside_data_store(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PublishTransactionError, match="metadata artifact path"):
        commit_publish_transaction(context, [], {"../manifest.json": {"status": "ready"}})

    assert not context.store_path("jobs", "publish.lock").exists()
    assert not context.store_path("jobs", ".publish-staging").exists()


def test_metadata_rejects_existing_case_alias_before_lock_or_staging(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    existing = context.store_path("metadata", "Report.json")
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b'{"status":"old"}\n')
    before = existing.read_bytes()

    with pytest.raises(PublishTransactionError, match="case-insensitive publish target"):
        commit_publish_transaction(
            context,
            [],
            {"metadata/report.json": {"status": "new"}},
        )

    assert existing.read_bytes() == before
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not context.store_path("jobs", ".publish-staging").exists()


def test_partition_and_metadata_case_aliases_fail_before_lock_or_formal_mutation(
    tmp_path
):
    context = DataAnalystsContext.from_paths(tmp_path)
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 1)],
        SAMPLE_SPEC,
        mode="replace",
    )
    transaction_root = _staging_root(staged[0].staged_path)

    with pytest.raises(PublishTransactionError, match="case-insensitive publish target"):
        commit_publish_transaction(
            context,
            staged,
            {
                "CANONICAL/DERIVED/SAMPLE/YEAR=2026/PART.PARQUET": {
                    "status": "must-not-publish"
                }
            },
        )

    assert not context.artifact_path(_artifact_path("2026")).exists()
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not transaction_root.exists()


@pytest.mark.parametrize(
    "reserved_path",
    [
        "jobs/publish.lock",
        "JOBS/PUBLISH.LOCK",
        "jobs/publish.lock::$DATA",
        "jobs/publish.lock.",
        "jobs/publish.lock ",
        "jobs/.publish-staging/foreign/payload.json",
        "jobs/.PUBLISH-STAGING/foreign/payload.json",
        "metadata/CON/report.json",
        "metadata/nul.json",
    ],
)
def test_metadata_targets_reject_transaction_control_surfaces_without_mutation(
    tmp_path, monkeypatch, reserved_path
):
    context = DataAnalystsContext.from_paths(tmp_path)
    partition_target = _write_parquet(
        context, _artifact_path("2026"), [_row("2026-01-02", "2330", 1)]
    )
    metadata_target = context.store_path("metadata", "sentinel.json")
    metadata_target.parent.mkdir(parents=True, exist_ok=True)
    metadata_target.write_bytes(b'{"sentinel":"formal"}\n')
    formal_paths = [partition_target, metadata_target]
    before_bytes = {path: path.read_bytes() for path in formal_paths}
    before_hashes = _hashes(formal_paths)
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    lock_path = context.store_path("jobs", "publish.lock")
    real_path_open = Path.open
    lock_acquisitions: list[Path] = []

    def recording_path_open(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if Path(path) == lock_path and mode == "x":
            lock_acquisitions.append(Path(path))
        return real_path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_path_open)

    with pytest.raises(PublishTransactionError, match="reserved metadata artifact path"):
        commit_publish_transaction(
            context,
            staged,
            {reserved_path: {"status": "must-not-publish"}},
        )

    assert {path: path.read_bytes() for path in formal_paths} == before_bytes
    assert _hashes(formal_paths) == before_hashes
    assert lock_acquisitions == []
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not context.store_path("jobs", ".publish-staging").exists()


def test_metadata_targets_allow_extensible_formal_metadata_namespaces(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    target = context.store_path("metadata", "extensions", "future_contract.json")

    commit_publish_transaction(
        context,
        [],
        {"metadata/extensions/future_contract.json": {"status": "ready"}},
    )

    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "ready"}
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not context.store_path("jobs", ".publish-staging").exists()


@pytest.mark.parametrize(
    "artifact_path",
    [
        "metadata/backups/report.json",
        "metadata/rollback_journal.json",
    ],
)
def test_metadata_targets_allow_control_names_outside_transaction_subtree(
    tmp_path, artifact_path
):
    context = DataAnalystsContext.from_paths(tmp_path)

    commit_publish_transaction(
        context,
        [],
        {artifact_path: {"status": "ready"}},
    )

    assert json.loads(
        context.artifact_path(artifact_path).read_text(encoding="utf-8")
    ) == {"status": "ready"}
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not context.store_path("jobs", ".publish-staging").exists()


def test_commit_revalidates_partition_formal_path_before_lock_or_mutation(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    formal_target = _write_parquet(
        context, _artifact_path("2026"), [_row("2026-01-02", "2330", 1)]
    )
    before_bytes = formal_target.read_bytes()
    before_hash = _hashes([formal_target])
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    transaction_root = _staging_root(staged[0].staged_path)
    forged_artifact_path = "jobs/.publish-staging/formal/part.parquet"
    forged_staged_path = (
        transaction_root / "partitions" / Path(forged_artifact_path)
    )
    forged_staged_path.parent.mkdir(parents=True, exist_ok=True)
    staged[0].staged_path.replace(forged_staged_path)
    forged = replace(
        staged[0],
        artifact_path=forged_artifact_path,
        staged_path=forged_staged_path,
        backup_path=transaction_root / "backups" / Path(forged_artifact_path),
    )

    with pytest.raises(PublishTransactionError, match="reserved formal artifact path"):
        commit_publish_transaction(context, [forged], {})

    assert formal_target.read_bytes() == before_bytes
    assert _hashes([formal_target]) == before_hash
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not transaction_root.exists()


def test_commit_publishes_metadata_after_all_partitions_and_cleans_up(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    staged = stage_partition_rows(
        context,
        [_row("2025-01-02", "2330", 1), _row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_root = _staging_root(staged[0].staged_path)
    real_replace = filesystem_module.os.replace
    replaced_targets: list[Path] = []

    def recording_replace(source, target):
        replaced_targets.append(Path(target).resolve())
        return real_replace(source, target)

    monkeypatch.setattr(filesystem_module.os, "replace", recording_replace)
    metadata = {
        "diagnostics/sample_evidence.json": {"status": "ready"},
        "manifests/sample.json": {"status": "ready", "artifact_paths": []},
    }

    commit_publish_transaction(context, staged, metadata)

    assert [path.suffix for path in replaced_targets[:2]] == [".parquet", ".parquet"]
    assert replaced_targets[2:] == [
        context.store_path("diagnostics", "sample_evidence.json"),
        context.store_path("manifests", "sample.json"),
    ]
    assert json.loads(
        context.store_path("manifests", "sample.json").read_text(encoding="utf-8")
    )["status"] == "ready"
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_root.exists()


def test_commit_failure_restores_all_formal_files(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    partition_targets = [
        _write_parquet(context, _artifact_path(year), [_row(f"{year}-01-02", "2330", 1)])
        for year in ("2025", "2026")
    ]
    metadata_payloads = {
        "diagnostics/sample_evidence.json": {"status": "old"},
        "manifests/sample.json": {"status": "old"},
    }
    metadata_targets = []
    for path, payload in metadata_payloads.items():
        target = context.artifact_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        metadata_targets.append(target)
    formal_targets = [*partition_targets, *metadata_targets]
    before = _hashes(formal_targets)
    staged = stage_partition_rows(
        context,
        [_row("2025-01-02", "2330", 2), _row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_root = _staging_root(staged[0].staged_path)
    real_replace = filesystem_module.os.replace
    partition_replaces = 0
    failure_injected = False

    def fail_on_second_partition_replace(source, target):
        nonlocal partition_replaces, failure_injected
        target_path = Path(target)
        if target_path.suffix == ".parquet" and not failure_injected:
            partition_replaces += 1
            if partition_replaces == 2:
                failure_injected = True
                raise OSError("injected commit failure")
        return real_replace(source, target)

    monkeypatch.setattr(filesystem_module.os, "replace", fail_on_second_partition_replace)

    with pytest.raises(PublishTransactionError, match="commit publish transaction"):
        commit_publish_transaction(
            context,
            staged,
            {
                "diagnostics/sample_evidence.json": {"status": "new"},
                "manifests/sample.json": {"status": "new"},
            },
        )

    assert _hashes(formal_targets) == before
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_root.exists()


def test_metadata_commit_failure_restores_partitions_and_prior_metadata(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    partition_target = _write_parquet(
        context, _artifact_path("2026"), [_row("2026-01-02", "2330", 1)]
    )
    metadata_paths = [
        "diagnostics/sample_evidence.json",
        "manifests/sample.json",
    ]
    metadata_targets: list[Path] = []
    for path in metadata_paths:
        target = context.artifact_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"status": "old"}), encoding="utf-8")
        metadata_targets.append(target)
    formal_targets = [partition_target, *metadata_targets]
    before = _hashes(formal_targets)
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_root = _staging_root(staged[0].staged_path)
    journal_path = staging_root / "rollback_journal.json"
    real_replace = filesystem_module.os.replace
    metadata_replaces = 0
    failure_injected = False

    def fail_second_metadata_replace(source, target):
        nonlocal metadata_replaces, failure_injected
        target_path = Path(target)
        if target_path.suffix == ".json" and not failure_injected:
            metadata_replaces += 1
            if metadata_replaces == 2:
                failure_injected = True
                assert journal_path.exists()
                raise OSError("injected metadata commit failure")
        return real_replace(source, target)

    monkeypatch.setattr(filesystem_module.os, "replace", fail_second_metadata_replace)

    with pytest.raises(PublishTransactionError, match="commit publish transaction"):
        commit_publish_transaction(
            context,
            staged,
            {
                "diagnostics/sample_evidence.json": {"status": "new"},
                "manifests/sample.json": {"status": "new"},
            },
        )

    assert _hashes(formal_targets) == before
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_root.exists()


def test_failure_after_new_target_creation_removes_new_file_and_restores_old_files(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    old_target = _write_parquet(
        context, _artifact_path("2026"), [_row("2026-01-02", "2330", 1)]
    )
    before = old_target.read_bytes()
    staged = stage_partition_rows(
        context,
        [_row("2025-01-02", "2330", 5), _row("2026-01-02", "2330", 6)],
        SAMPLE_SPEC,
        mode="replace",
    )
    new_target = context.artifact_path(_artifact_path("2025"))
    real_replace = filesystem_module.os.replace
    calls = 0
    failure_injected = False

    def fail_second_partition_replace(source, target):
        nonlocal calls, failure_injected
        if Path(target).suffix == ".parquet" and not failure_injected:
            calls += 1
            if calls == 2:
                failure_injected = True
                raise OSError("injected failure")
        return real_replace(source, target)

    monkeypatch.setattr(filesystem_module.os, "replace", fail_second_partition_replace)

    with pytest.raises(PublishTransactionError):
        commit_publish_transaction(context, staged, {})

    assert not new_target.exists()
    assert old_target.read_bytes() == before


def test_lock_cleanup_failure_is_reported_instead_of_success(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    _write_parquet(
        context,
        _artifact_path("2026"),
        [_row("2026-01-01", "2330", 1)],
    )
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_root = _staging_root(staged[0].staged_path)
    lock_path = context.store_path("jobs", "publish.lock")
    real_unlink = Path.unlink

    def fail_lock_unlink(path, *args, **kwargs):
        if Path(path) == lock_path:
            raise PermissionError("injected lock cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_lock_unlink)

    with pytest.raises(PublishTransactionError, match="lock cleanup.*injected") as exc_info:
        commit_publish_transaction(context, staged, {})

    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8") == staging_root.name
    assert staging_root.exists()
    assert (staging_root / "rollback_journal.json").exists()
    assert any((staging_root / "backups").rglob("*.parquet"))
    assert str(staging_root) in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, PermissionError)


def test_staging_cleanup_failure_is_reported_instead_of_success(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_root = _staging_root(staged[0].staged_path)
    real_rmtree = transactions.shutil.rmtree

    def fail_transaction_cleanup(path, *args, **kwargs):
        if Path(path) == staging_root:
            raise PermissionError("injected staging cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(transactions.shutil, "rmtree", fail_transaction_cleanup)

    with pytest.raises(PublishTransactionError, match="staging cleanup.*injected"):
        commit_publish_transaction(context, staged, {})

    assert staging_root.exists()
    assert (staging_root / "rollback_journal.json").exists()
    assert not context.store_path("jobs", "publish.lock").exists()


def test_forward_commit_retries_transient_windows_permission_error(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    target = _write_parquet(
        context,
        _artifact_path("2026"),
        [_row("2026-01-01", "2330", 1)],
    )
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    expected_bytes = staged[0].staged_path.read_bytes()
    staging_root = _staging_root(staged[0].staged_path)
    real_replace = filesystem_module.os.replace
    forward_attempts = 0

    def transient_forward_failure(source, destination):
        nonlocal forward_attempts
        source_path = Path(source)
        if source_path.is_relative_to(staging_root / "partitions"):
            forward_attempts += 1
            if forward_attempts <= 2:
                raise PermissionError(13, "injected transient forward failure", str(source), 5)
        return real_replace(source, destination)

    monkeypatch.setattr(filesystem_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(filesystem_module.os, "replace", transient_forward_failure)

    commit_publish_transaction(context, staged, {})

    assert forward_attempts == 3
    assert target.read_bytes() == expected_bytes
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_root.exists()


def test_rollback_restore_retries_transient_windows_permission_error(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    targets = [
        _write_parquet(
            context,
            _artifact_path(year),
            [_row(f"{year}-01-01", "2330", 1)],
        )
        for year in ("2025", "2026")
    ]
    before_bytes = {target: target.read_bytes() for target in targets}
    staged = stage_partition_rows(
        context,
        [_row("2025-01-02", "2330", 5), _row("2026-01-02", "2330", 6)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_root = _staging_root(staged[0].staged_path)
    real_replace = filesystem_module.os.replace
    forward_attempts = 0
    rollback_attempts = 0

    def fail_forward_then_transient_rollback(source, destination):
        nonlocal forward_attempts, rollback_attempts
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path.is_relative_to(staging_root / "partitions"):
            forward_attempts += 1
            if forward_attempts == 2:
                raise OSError("injected forward commit failure")
        if (
            source_path.is_relative_to(staging_root / "backups")
            and destination_path == targets[0]
        ):
            rollback_attempts += 1
            if rollback_attempts <= 2:
                raise PermissionError(
                    13, "injected transient rollback failure", str(source), 5
                )
        return real_replace(source, destination)

    monkeypatch.setattr(filesystem_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        filesystem_module.os, "replace", fail_forward_then_transient_rollback
    )

    with pytest.raises(PublishTransactionError, match="commit publish transaction"):
        commit_publish_transaction(context, staged, {})

    assert rollback_attempts == 3
    assert {target: target.read_bytes() for target in targets} == before_bytes
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_root.exists()


def test_permanent_windows_permission_error_fails_closed_with_recovery_state(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    target = _write_parquet(
        context,
        _artifact_path("2026"),
        [_row("2026-01-01", "2330", 1)],
    )
    before_bytes = target.read_bytes()
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_root = _staging_root(staged[0].staged_path)
    lock_path = context.store_path("jobs", "publish.lock")
    forward_attempts = 0
    rollback_attempts = 0

    def permanent_failure(source, destination):
        nonlocal forward_attempts, rollback_attempts
        source_path = Path(source)
        if source_path.is_relative_to(staging_root / "partitions"):
            forward_attempts += 1
        elif source_path.is_relative_to(staging_root / "backups"):
            rollback_attempts += 1
        raise PermissionError(13, "injected permanent replace failure", str(source), 5)

    monkeypatch.setattr(filesystem_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(filesystem_module.os, "replace", permanent_failure)

    with pytest.raises(PublishTransactionError, match="rollback was incomplete") as exc_info:
        commit_publish_transaction(context, staged, {})

    assert forward_attempts == 4
    assert rollback_attempts == 4
    assert isinstance(exc_info.value.__cause__, PermissionError)
    assert "injected permanent replace failure" in str(exc_info.value)
    assert target.read_bytes() == before_bytes
    assert lock_path.read_text(encoding="utf-8") == staging_root.name
    journal = json.loads(
        (staging_root / "rollback_journal.json").read_text(encoding="utf-8")
    )
    assert journal["status"] == "committing"
    backups = list((staging_root / "backups").rglob("*.parquet"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before_bytes


@pytest.mark.parametrize(
    ("permission_error", "winerror"),
    [(True, 33), (False, 5)],
    ids=["permission-other-winerror", "non-permission-winerror-5"],
)
def test_replace_does_not_retry_non_transient_errors(
    tmp_path, monkeypatch, permission_error, winerror
):
    context = DataAnalystsContext.from_paths(tmp_path)
    target = _write_parquet(
        context,
        _artifact_path("2026"),
        [_row("2026-01-01", "2330", 1)],
    )
    before_bytes = target.read_bytes()
    staged = stage_partition_rows(
        context,
        [_row("2026-01-02", "2330", 2)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_root = _staging_root(staged[0].staged_path)
    real_replace = filesystem_module.os.replace
    forward_attempts = 0

    def non_transient_failure(source, destination):
        nonlocal forward_attempts
        source_path = Path(source)
        if source_path.is_relative_to(staging_root / "partitions"):
            forward_attempts += 1
            if permission_error:
                raise PermissionError(
                    13, "injected non-transient permission error", str(source), winerror
                )
            error = OSError("injected non-permission error")
            error.winerror = winerror
            raise error
        return real_replace(source, destination)

    monkeypatch.setattr(filesystem_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(filesystem_module.os, "replace", non_transient_failure)

    with pytest.raises(PublishTransactionError, match="commit publish transaction"):
        commit_publish_transaction(context, staged, {})

    assert forward_attempts == 1
    assert target.read_bytes() == before_bytes
    assert not context.store_path("jobs", "publish.lock").exists()
    assert not staging_root.exists()


def test_incomplete_rollback_preserves_lock_journal_and_backups(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    for year in ("2025", "2026"):
        _write_parquet(
            context,
            _artifact_path(year),
            [_row(f"{year}-01-01", "2330", 1)],
        )
    staged = stage_partition_rows(
        context,
        [_row("2025-01-02", "2330", 5), _row("2026-01-02", "2330", 6)],
        SAMPLE_SPEC,
        mode="replace",
    )
    staging_root = _staging_root(staged[0].staged_path)
    lock_path = context.store_path("jobs", "publish.lock")
    real_replace = filesystem_module.os.replace
    formal_attempts = 0

    def fail_commit_then_rollback(source, target):
        nonlocal formal_attempts
        source_path = Path(source)
        target_path = Path(target)
        if source_path.is_relative_to(staging_root / "partitions"):
            formal_attempts += 1
            if formal_attempts == 2:
                raise OSError("injected commit failure")
        if source_path.is_relative_to(
            staging_root / "backups"
        ) and target_path == context.artifact_path(_artifact_path("2025")):
            raise PermissionError("injected rollback failure")
        return real_replace(source, target)

    monkeypatch.setattr(filesystem_module.os, "replace", fail_commit_then_rollback)

    with pytest.raises(PublishTransactionError, match="rollback was incomplete") as exc_info:
        commit_publish_transaction(context, staged, {})

    assert isinstance(exc_info.value.__cause__, OSError)
    assert str(staging_root) in str(exc_info.value)
    assert lock_path.exists()
    assert (staging_root / "rollback_journal.json").exists()
    assert any((staging_root / "backups").rglob("*.parquet"))

    with pytest.raises(PublishTransactionError, match="publish lock already exists"):
        commit_publish_transaction(context, staged, {})

    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8") == staging_root.name
    assert staging_root.exists()
    assert (staging_root / "rollback_journal.json").exists()
    assert any((staging_root / "backups").rglob("*.parquet"))
