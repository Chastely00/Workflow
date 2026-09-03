from dataclasses import replace
from datetime import date, datetime

import pandas as pd
import pytest

from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.artifacts import ArtifactError, validate_rows


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


def tradability_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "date": "2026-07-08",
        "ticker": "2330",
        "source_available_date": "2026-07-08",
        "data_cutoff_at": "2026-07-08T10:00:00Z",
    }
    row.update(overrides)
    return row


def test_valid_rows_are_accepted(contract: ArtifactContract):
    validate_rows(contract, [tradability_row()], partition_value="2026")


def test_missing_required_column_fails_closed(contract: ArtifactContract):
    row = tradability_row()
    del row["source_available_date"]

    with pytest.raises(ArtifactError, match="missing required column.*source_available_date"):
        validate_rows(contract, [row], partition_value="2026")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_logical_key_value_fails_closed(contract: ArtifactContract, value: object):
    with pytest.raises(ArtifactError, match="logical key.*ticker"):
        validate_rows(contract, [tradability_row(ticker=value)], partition_value="2026")


def test_single_nan_logical_key_fails_closed(contract: ArtifactContract):
    with pytest.raises(ArtifactError, match="logical key.*ticker"):
        validate_rows(
            contract,
            [tradability_row(ticker=float("nan"))],
            partition_value="2026",
        )


def test_duplicate_nan_logical_keys_fail_closed(contract: ArtifactContract):
    with pytest.raises(ArtifactError, match="logical key.*ticker"):
        validate_rows(
            contract,
            [
                tradability_row(ticker=float("nan")),
                tradability_row(ticker=float("nan")),
            ],
            partition_value="2026",
        )


@pytest.mark.parametrize("value", [pd.NA, pd.NaT])
def test_pandas_missing_logical_key_fails_closed(
    contract: ArtifactContract,
    value: object,
):
    with pytest.raises(ArtifactError, match="logical key.*ticker"):
        validate_rows(contract, [tradability_row(ticker=value)], partition_value="2026")


def test_missing_partition_field_fails_closed(contract: ArtifactContract):
    row = tradability_row()
    del row["date"]

    with pytest.raises(ArtifactError, match="partition field.*date"):
        validate_rows(contract, [row], partition_value="2026")


@pytest.mark.parametrize("field", ["date", "source_available_date"])
def test_invalid_iso_date_fails_closed(contract: ArtifactContract, field: str):
    with pytest.raises(ArtifactError, match=field):
        validate_rows(contract, [tradability_row(**{field: "2026-02-30"})], partition_value="2026")


def test_iso_date_objects_are_accepted(contract: ArtifactContract):
    validate_rows(
        contract,
        [
            tradability_row(
                date=date(2026, 7, 8),
                source_available_date=datetime(2026, 7, 8, 10, 0),
            )
        ],
        partition_value="2026",
    )


def test_date_field_rejects_valid_prefix_with_trailing_garbage(contract: ArtifactContract):
    with pytest.raises(ArtifactError, match="date"):
        validate_rows(
            contract,
            [tradability_row(date="2026-07-08garbage")],
            partition_value="2026",
        )


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "1970-01-01T00:00:00Z", "1970-01-01T08:00:00+08:00"],
)
def test_missing_or_sentinel_cutoff_fails_closed(contract: ArtifactContract, value: object):
    with pytest.raises(ArtifactError, match="data_cutoff_at"):
        validate_rows(contract, [tradability_row(data_cutoff_at=value)], partition_value="2026")


def test_malformed_cutoff_fails_closed(contract: ArtifactContract):
    with pytest.raises(ArtifactError, match="data_cutoff_at"):
        validate_rows(
            contract,
            [tradability_row(data_cutoff_at="not-a-timestamp")],
            partition_value="2026",
        )


def test_wrong_year_row_is_rejected(contract: ArtifactContract):
    rows = [tradability_row(date="2025-12-31")]
    with pytest.raises(ArtifactError, match="partition"):
        validate_rows(contract, rows, partition_value="2026")


def test_duplicate_logical_key_in_batch_is_rejected(contract: ArtifactContract):
    row = tradability_row()
    with pytest.raises(ArtifactError, match="duplicate logical key"):
        validate_rows(contract, [row, dict(row)], partition_value="2026")


def test_semantically_duplicate_date_key_is_rejected(contract: ArtifactContract):
    first = tradability_row()
    second = tradability_row(date=date(2026, 7, 8))

    with pytest.raises(ArtifactError, match="duplicate logical key"):
        validate_rows(contract, [first, second], partition_value="2026")


def test_exact_date_partition_uses_full_iso_date(contract: ArtifactContract):
    exact_date_contract = replace(
        contract,
        publication_mode="snapshot_by_value",
        partition_name="as_of_date",
    )

    validate_rows(
        exact_date_contract,
        [tradability_row()],
        partition_value="2026-07-08",
    )
