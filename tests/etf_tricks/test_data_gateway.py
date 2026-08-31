from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from etf_tricks.calendar import CalendarContractError, TradingCalendar
from etf_tricks.data_gateway import DataContractError, DataGateway


_SHA256 = "a" * 64
_MARKET_STATE_COLUMNS = (
    "date",
    "ticker",
    "market",
    "market_state",
    "state_reason",
    "amount_state",
    "authoritative_traded_value",
    "amount_zero_authorized",
    "price_row_present",
    "attr_row_present",
    "atten_fg",
    "disp_fg",
    "full_fg",
    "limit_fg",
    "limo_fg",
    "sbadt_fg",
    "ssadt_fg",
    "susp_fg",
    "exchange_tradable",
    "full_delivery",
    "instrument_kind",
    "identity_source",
    "security_master_market",
    "lifecycle_list_date",
    "lifecycle_delist_date",
    "lifecycle_interval_start",
    "lifecycle_interval_end_exclusive",
    "lifecycle_active",
    "lifecycle_conflict",
    "identity_conflict",
    "lifecycle_pit_status",
    "revision_pit_status",
    "observation_date",
    "source_available_date",
    "availability_precision",
    "earliest_execution_session",
    "security_master_manifest_sha256",
    "calendar_manifest_sha256",
    "price_manifest_sha256",
    "tradability_manifest_sha256",
    "classification_policy_version",
    "data_cutoff_at",
)


def _market_state_rows() -> list[dict[str, object]]:
    return [
        {
            "date": "2025-01-02",
            "ticker": "1101",
            "market": "TWSE",
            "market_state": "TRADING",
            "state_reason": "APIPRCD_OBSERVED_AMOUNT",
            "amount_state": "OBSERVED",
            "authoritative_traded_value": 1_000.0,
            "amount_zero_authorized": False,
            "price_row_present": True,
            "attr_row_present": True,
            "atten_fg": "N",
            "disp_fg": "N",
            "full_fg": "N",
            "limit_fg": "N",
            "limo_fg": "N",
            "sbadt_fg": "N",
            "ssadt_fg": "N",
            "susp_fg": "N",
            "exchange_tradable": True,
            "full_delivery": False,
            "instrument_kind": "EQUITY",
            "identity_source": "SECURITY_MASTER_SNAPSHOT",
            "security_master_market": "TWSE",
            "lifecycle_list_date": "2000-01-01",
            "lifecycle_delist_date": None,
            "lifecycle_interval_start": "2025-01-01",
            "lifecycle_interval_end_exclusive": "2025-01-07",
            "lifecycle_active": True,
            "lifecycle_conflict": False,
            "identity_conflict": False,
            "lifecycle_pit_status": "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED",
            "revision_pit_status": "PIT_REVISION_UNVERIFIED",
            "observation_date": "2025-01-02",
            "source_available_date": "2025-01-02",
            "availability_precision": "AFTER_CLOSE_DATE_ONLY",
            "earliest_execution_session": "2025-01-03",
            "security_master_manifest_sha256": _SHA256,
            "calendar_manifest_sha256": _SHA256,
            "price_manifest_sha256": _SHA256,
            "tradability_manifest_sha256": _SHA256,
            "classification_policy_version": "daily_market_state_v3",
            "data_cutoff_at": "2025-01-02T14:00:00Z",
        },
        {
            "date": "2025-01-03",
            "ticker": "1101",
            "market": "TWSE",
            "market_state": "HALTED",
            "state_reason": "APISTKATTR_SUSPENSION_NO_PRICE",
            "amount_state": "ZERO_AUTHORIZED",
            "authoritative_traded_value": 0.0,
            "amount_zero_authorized": True,
            "price_row_present": False,
            "attr_row_present": True,
            "atten_fg": "N",
            "disp_fg": "N",
            "full_fg": "N",
            "limit_fg": "N",
            "limo_fg": "N",
            "sbadt_fg": "N",
            "ssadt_fg": "N",
            "susp_fg": "Y",
            "exchange_tradable": False,
            "full_delivery": False,
            "instrument_kind": "EQUITY",
            "identity_source": "SECURITY_MASTER_SNAPSHOT",
            "security_master_market": "TWSE",
            "lifecycle_list_date": "2000-01-01",
            "lifecycle_delist_date": None,
            "lifecycle_interval_start": "2025-01-01",
            "lifecycle_interval_end_exclusive": "2025-01-07",
            "lifecycle_active": True,
            "lifecycle_conflict": False,
            "identity_conflict": False,
            "lifecycle_pit_status": "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED",
            "revision_pit_status": "PIT_REVISION_UNVERIFIED",
            "observation_date": "2025-01-03",
            "source_available_date": "2025-01-03",
            "availability_precision": "AFTER_CLOSE_DATE_ONLY",
            "earliest_execution_session": "2025-01-06",
            "security_master_manifest_sha256": _SHA256,
            "calendar_manifest_sha256": _SHA256,
            "price_manifest_sha256": _SHA256,
            "tradability_manifest_sha256": _SHA256,
            "classification_policy_version": "daily_market_state_v3",
            "data_cutoff_at": "2025-01-03T14:00:00Z",
        },
    ]


def _write_market_state_artifact(
    root: Path,
    *,
    rows: list[dict[str, object]] | None = None,
    status: str = "ready",
    date_range: tuple[str, str] | None = ("2025-01-02", "2025-01-03"),
    manifest_overrides: dict[str, object] | None = None,
) -> Path:
    frame = pd.DataFrame(rows or _market_state_rows(), columns=_MARKET_STATE_COLUMNS)
    artifact_path = "canonical/derived/daily_market_state/year=2025/part.parquet"
    manifest_dir = root / "data_store" / "manifests"
    parquet_path = root / "data_store" / artifact_path
    manifest_dir.mkdir(parents=True, exist_ok=True)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(parquet_path, index=False)
    content_sha256 = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
    schema_fingerprint = hashlib.sha256(
        pq.read_schema(parquet_path).serialize().to_pybytes()
    ).hexdigest()
    manifest = {
        "artifact_id": "daily_market_state",
        "artifact_paths": [artifact_path],
        "columns": list(_MARKET_STATE_COLUMNS),
        "date_range": ["2025-01-02", "2025-01-03"],
        "availability_date_range": ["2025-01-02", "2025-01-03"],
        "status": status,
        "row_count": len(frame),
        "duplicate_count": 0,
        "logical_key": ["date", "ticker"],
        "schema_version": "1.0",
        "schema_fingerprint": schema_fingerprint,
        "active_version": "market-state-v3",
        "source_families": [
            "security_master",
            "trading_calendar",
            "daily_price_volume",
            "daily_tradability",
        ],
        "dependency_versions": {
            "security_master": "security-master-v1",
            "trading_calendar": "calendar-v1",
            "daily_price_volume": "dpv-v1",
            "daily_tradability": "tradability-v1",
        },
        "dependency_manifest_sha256_by_contract": {
            "security_master": _SHA256,
            "trading_calendar": _SHA256,
            "daily_price_volume": _SHA256,
            "daily_tradability": _SHA256,
        },
        "dependency_certification_fingerprint": "certification-v1",
        "build_start": "2025-01-01",
        "build_end": "2025-01-06",
        "certified_source_start": "2024-01-01",
        "classification_policy_version": "daily_market_state_v3",
        "state_lattice_policy_version": "daily_market_state_lattice_v5",
        "market_identity_policy_version": "daily_market_identity_v3",
        "partition_inventory": [
            {
                "partition_value": "2025",
                "path": artifact_path,
                "size": parquet_path.stat().st_size,
                "row_count": len(frame),
                "schema_fingerprint": schema_fingerprint,
                "content_sha256": content_sha256,
            }
        ],
    }
    manifest.update(manifest_overrides or {})
    (manifest_dir / "daily_market_state.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return parquet_path


def _refresh_market_state_inventory(root: Path, parquet_path: Path) -> None:
    manifest_path = root / "data_store" / "manifests" / "daily_market_state.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    table = pq.read_table(parquet_path)
    manifest["row_count"] = table.num_rows
    manifest["columns"] = table.schema.names
    manifest["schema_fingerprint"] = hashlib.sha256(
        table.schema.serialize().to_pybytes()
    ).hexdigest()
    manifest["partition_inventory"] = [
        {
            "partition_value": "2025",
            "path": manifest["artifact_paths"][0],
            "size": parquet_path.stat().st_size,
            "row_count": table.num_rows,
            "schema_fingerprint": manifest["schema_fingerprint"],
            "content_sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
        }
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _replace_market_state_column(
    root: Path,
    parquet_path: Path,
    column: str,
    values: list[object],
    value_type: pa.DataType,
) -> None:
    table = pq.read_table(parquet_path)
    index = table.schema.get_field_index(column)
    table = table.set_column(
        index,
        pa.field(column, value_type, nullable=True),
        pa.array(values, type=value_type),
    )
    pq.write_table(table, parquet_path)
    _refresh_market_state_inventory(root, parquet_path)


def _write_artifact(
    root: Path,
    *,
    artifact_id: str = "prices",
    status: str = "ready",
    columns: tuple[str, ...] = ("date", "ticker", "close"),
    artifact_path: str = "canonical/raw/prices/year=2025/part.parquet",
    date_range: tuple[str, str] | None = ("2025-01-02", "2025-01-06"),
    row_count: int = 3,
    duplicate_count: int = 0,
) -> Path:
    manifest_dir = root / "data_store" / "manifests"
    parquet_path = root / "data_store" / artifact_path
    manifest_dir.mkdir(parents=True, exist_ok=True)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2025-01-02", "2025-01-03", "2025-01-06"],
            "ticker": ["1101", "1101", "1101"],
            "close": [10.0, 10.5, 11.0],
        }
    ).to_parquet(parquet_path, index=False)
    manifest = {
        "artifact_id": artifact_id,
        "artifact_paths": [artifact_path],
        "columns": list(columns),
        "date_range": list(date_range) if date_range else None,
        "availability_date_range": None,
        "status": status,
        "row_count": row_count,
        "duplicate_count": duplicate_count,
        "logical_key": ["date", "ticker"],
    }
    (manifest_dir / f"{artifact_id}.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return parquet_path


def test_gateway_reads_only_manifest_declared_columns_and_dates(tmp_path: Path) -> None:
    _write_artifact(tmp_path)
    gateway = DataGateway.from_data_analysts(tmp_path)

    result = gateway.read_artifact(
        "prices",
        columns=("date", "ticker", "close"),
        start="2025-01-03",
        end="2025-01-06",
    )

    assert result.to_dict("records") == [
        {"date": pd.Timestamp("2025-01-03"), "ticker": "1101", "close": 10.5},
        {"date": pd.Timestamp("2025-01-06"), "ticker": "1101", "close": 11.0},
    ]


def test_gateway_rejects_non_ready_manifest(tmp_path: Path) -> None:
    _write_artifact(tmp_path, status="failed")

    with pytest.raises(DataContractError, match="prices.*status.*failed"):
        DataGateway.from_data_analysts(tmp_path).read_artifact("prices")


def test_gateway_rejects_requested_column_missing_from_manifest(tmp_path: Path) -> None:
    _write_artifact(tmp_path)

    with pytest.raises(DataContractError, match="prices.*adj_close"):
        DataGateway.from_data_analysts(tmp_path).read_artifact(
            "prices", columns=("date", "ticker", "adj_close")
        )


def test_gateway_rejects_manifest_path_outside_data_store(tmp_path: Path) -> None:
    _write_artifact(tmp_path, artifact_path="../outside.parquet")

    with pytest.raises(DataContractError, match="outside data_store"):
        DataGateway.from_data_analysts(tmp_path).read_artifact("prices")


def test_gateway_rejects_requested_coverage_outside_manifest_range(tmp_path: Path) -> None:
    _write_artifact(tmp_path)

    with pytest.raises(DataContractError, match="prices.*coverage"):
        DataGateway.from_data_analysts(tmp_path).read_artifact(
            "prices", start="2025-01-01", end="2025-01-06"
        )


def test_gateway_rejects_requested_bounds_without_coverage_metadata(tmp_path: Path) -> None:
    _write_artifact(tmp_path, date_range=None)

    with pytest.raises(DataContractError, match="coverage metadata"):
        DataGateway.from_data_analysts(tmp_path).read_artifact(
            "prices", start="2025-01-02", end="2025-01-06"
        )


def test_gateway_rejects_manifest_or_physical_row_count_mismatch(tmp_path: Path) -> None:
    _write_artifact(tmp_path, row_count=4)

    with pytest.raises(DataContractError, match="row_count"):
        DataGateway.from_data_analysts(tmp_path).read_artifact("prices")


def test_gateway_rejects_declared_or_physical_duplicate_logical_keys(tmp_path: Path) -> None:
    _write_artifact(tmp_path, duplicate_count=1)
    with pytest.raises(DataContractError, match="duplicate_count"):
        DataGateway.from_data_analysts(tmp_path).read_artifact("prices")

    path = _write_artifact(tmp_path)
    duplicate = pd.read_parquet(path)
    duplicate.loc[1, ["date", "ticker"]] = duplicate.loc[0, ["date", "ticker"]]
    duplicate.to_parquet(path, index=False)
    with pytest.raises(DataContractError, match="duplicate logical key"):
        DataGateway.from_data_analysts(tmp_path).read_artifact("prices")


def test_filtered_scan_reads_only_matching_rows_and_normalizes_string_dates(
    tmp_path: Path,
) -> None:
    path = _write_artifact(
        tmp_path,
        artifact_id="daily_price_volume",
        columns=("date", "ticker", "close", "traded_value"),
        date_range=("2025-01-02", "2025-01-06"),
        row_count=4,
    )
    pd.DataFrame(
        {
            "date": ["2025-01-02", "2025-01-03", "2025-01-03", "2025-01-06"],
            "ticker": ["IX0001", "IX0001", "1101", "IX0001"],
            "close": [100.0, 101.0, 10.5, 102.0],
            "traded_value": [1_000.0, 1_100.0, 50.0, 1_200.0],
        }
    ).to_parquet(path, index=False)

    frame = DataGateway.from_data_analysts(tmp_path).scan_artifact(
        "daily_price_volume",
        columns=["date", "ticker", "close", "traded_value"],
        filters=[("ticker", "==", "IX0001")],
        start=pd.Timestamp("2025-01-03"),
        end=pd.Timestamp("2025-01-06"),
    )

    assert frame["ticker"].unique().tolist() == ["IX0001"]
    assert frame["date"].tolist() == [
        pd.Timestamp("2025-01-03"),
        pd.Timestamp("2025-01-06"),
    ]
    assert pd.api.types.is_datetime64_any_dtype(frame["date"])


def test_filtered_scan_rejects_unsupported_filter_operator(tmp_path: Path) -> None:
    _write_artifact(tmp_path)

    with pytest.raises(DataContractError, match="unsupported filter operator"):
        DataGateway.from_data_analysts(tmp_path).scan_artifact(
            "prices",
            columns=["date", "ticker"],
            filters=[("ticker", "contains", "11")],
        )


def test_scan_market_state_returns_exact_governed_contract(tmp_path: Path) -> None:
    _write_market_state_artifact(tmp_path)

    result = DataGateway.from_data_analysts(tmp_path).scan_market_state(
        "2025-01-02", "2025-01-03", ["1101"]
    )

    assert result.columns.tolist() == list(_MARKET_STATE_COLUMNS)
    assert result[["date", "ticker"]].to_dict("records") == [
        {"date": pd.Timestamp("2025-01-02"), "ticker": "1101"},
        {"date": pd.Timestamp("2025-01-03"), "ticker": "1101"},
    ]
    assert not result.duplicated(["date", "ticker"]).any()
    for column in (
        "date",
        "lifecycle_list_date",
        "lifecycle_delist_date",
        "lifecycle_interval_start",
        "lifecycle_interval_end_exclusive",
        "observation_date",
        "source_available_date",
        "earliest_execution_session",
    ):
        assert pd.api.types.is_datetime64_any_dtype(result[column])


def test_scan_market_state_accepts_manifest_column_permutation_but_projects_order(
    tmp_path: Path,
) -> None:
    _write_market_state_artifact(
        tmp_path,
        manifest_overrides={"columns": list(reversed(_MARKET_STATE_COLUMNS))},
    )

    result = DataGateway.from_data_analysts(tmp_path).scan_market_state(
        "2025-01-02", "2025-01-03"
    )

    assert result.columns.tolist() == list(_MARKET_STATE_COLUMNS)


@pytest.mark.parametrize(
    ("manifest_overrides", "error"),
    [
        (
            {
                "source_families": [
                    "security_master",
                    "trading_calendar",
                    "daily_price_volume",
                    "daily_tradability",
                    "unapproved_source",
                ]
            },
            "source_families",
        ),
        ({"dependency_versions": {}}, "dependency_versions"),
        ({"dependency_certification_fingerprint": ""}, "certification"),
        ({"build_start": "2025-01-07"}, "build"),
        ({"columns": [*_MARKET_STATE_COLUMNS, "official_market_status"]}, "schema"),
    ],
)
def test_scan_market_state_rejects_incomplete_or_unapproved_manifest_authority(
    tmp_path: Path,
    manifest_overrides: dict[str, object],
    error: str,
) -> None:
    _write_market_state_artifact(tmp_path, manifest_overrides=manifest_overrides)

    with pytest.raises(DataContractError, match=error):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-03"
        )


def test_scan_market_state_uses_build_coverage_not_observed_row_range(tmp_path: Path) -> None:
    _write_market_state_artifact(tmp_path)

    result = DataGateway.from_data_analysts(tmp_path).scan_market_state(
        "2025-01-01", "2025-01-06", []
    )

    assert result.empty


def test_scan_market_state_rejects_reversed_bounds(tmp_path: Path) -> None:
    _write_market_state_artifact(tmp_path)

    with pytest.raises(DataContractError, match="start.*end"):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-06", "2025-01-01"
        )


def test_scan_market_state_rejects_partition_digest_mismatch(tmp_path: Path) -> None:
    path = _write_market_state_artifact(tmp_path)
    path.write_bytes(b"tampered")

    with pytest.raises(DataContractError, match="content_sha256"):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-03"
        )


def test_scan_market_state_rejects_declared_schema_fingerprint_mismatch(
    tmp_path: Path,
) -> None:
    _write_market_state_artifact(tmp_path)
    manifest_path = tmp_path / "data_store" / "manifests" / "daily_market_state.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["partition_inventory"][0]["schema_fingerprint"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DataContractError, match="schema_fingerprint"):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-03"
        )


def test_scan_market_state_rejects_extra_physical_official_field(
    tmp_path: Path,
) -> None:
    path = _write_market_state_artifact(tmp_path)
    table = pq.read_table(path).append_column(
        "official_market_status", pa.array([None, None], type=pa.string())
    )
    pq.write_table(table, path)
    _refresh_market_state_inventory(tmp_path, path)
    manifest_path = tmp_path / "data_store" / "manifests" / "daily_market_state.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["columns"] = list(_MARKET_STATE_COLUMNS)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DataContractError, match="physical schema.*governed columns"):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-03"
        )


@pytest.mark.parametrize(
    ("manifest_overrides", "row", "error"),
    [
        (
            {"classification_policy_version": "daily_market_state_v2"},
            None,
            "classification_policy_version",
        ),
        (
            None,
            {**_market_state_rows()[0], "classification_policy_version": "daily_market_state_v2"},
            "classification_policy_version",
        ),
    ],
)
def test_scan_market_state_requires_governed_manifest_and_row_policy_version(
    tmp_path: Path,
    manifest_overrides: dict[str, object] | None,
    row: dict[str, object] | None,
    error: str,
) -> None:
    _write_market_state_artifact(
        tmp_path,
        rows=[row] if row is not None else None,
        manifest_overrides=manifest_overrides,
    )

    with pytest.raises(DataContractError, match=error):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-02"
        )


@pytest.mark.parametrize(
    ("mutate_manifest", "error"),
    [
        (
            lambda manifest: manifest["partition_inventory"][0].__setitem__("row_count", 99),
            "partition_inventory.*row_count",
        ),
        (
            lambda manifest: manifest.__setitem__("row_count", 99),
            "row_count",
        ),
    ],
)
def test_scan_market_state_checks_partition_and_top_level_row_counts_for_filtered_scan(
    tmp_path: Path,
    mutate_manifest: object,
    error: str,
) -> None:
    _write_market_state_artifact(tmp_path)
    manifest_path = tmp_path / "data_store" / "manifests" / "daily_market_state.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate_manifest(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DataContractError, match=error):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-02", ["1101"]
        )


def test_scan_market_state_rejects_manifest_drift_after_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_market_state_artifact(tmp_path)
    manifest_path = tmp_path / "data_store" / "manifests" / "daily_market_state.json"
    original = DataGateway.scan_artifact

    def scan_then_drift(
        self: DataGateway,
        artifact_id: str,
        **kwargs: object,
    ) -> pd.DataFrame:
        result = original(self, artifact_id, **kwargs)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["build_end"] = "2025-01-07"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        return result

    monkeypatch.setattr(DataGateway, "scan_artifact", scan_then_drift)

    with pytest.raises(DataContractError, match="manifest drift"):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-03"
        )


def test_scan_market_state_accepts_null_full_delivery_when_attribute_row_is_absent(
    tmp_path: Path,
) -> None:
    row = {
        **_market_state_rows()[0],
        "attr_row_present": False,
        "atten_fg": None,
        "disp_fg": None,
        "full_fg": None,
        "limit_fg": None,
        "limo_fg": None,
        "sbadt_fg": None,
        "ssadt_fg": None,
        "susp_fg": None,
        "full_delivery": None,
    }
    _write_market_state_artifact(tmp_path, rows=[row])

    result = DataGateway.from_data_analysts(tmp_path).scan_market_state(
        "2025-01-02", "2025-01-02"
    )

    assert result.loc[0, "full_delivery"] is None


@pytest.mark.parametrize(
    ("row", "error"),
    [
        (
            {**_market_state_rows()[0], "state_reason": "APISTKATTR_SUSPENSION_NO_PRICE"},
            "state_reason",
        ),
        (
            {**_market_state_rows()[0], "full_fg": "Y", "full_delivery": False},
            "full_delivery",
        ),
        (
            {
                **_market_state_rows()[0],
                "market_state": "HALTED",
                "state_reason": "APISTKATTR_SUSPENSION_WITH_OBSERVED_AMOUNT",
                "susp_fg": "Y",
                "exchange_tradable": False,
                "attr_row_present": False,
            },
            "attr_row_present",
        ),
    ],
)
def test_scan_market_state_rejects_classifier_matrix_mismatch(
    tmp_path: Path,
    row: dict[str, object],
    error: str,
) -> None:
    _write_market_state_artifact(tmp_path, rows=[row])

    with pytest.raises(DataContractError, match=error):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-02"
        )


def test_scan_market_state_accepts_suspended_price_row_with_observed_zero_amount(
    tmp_path: Path,
) -> None:
    row = {
        **_market_state_rows()[0],
        "market_state": "HALTED",
        "state_reason": "APISTKATTR_SUSPENSION_WITH_OBSERVED_AMOUNT",
        "authoritative_traded_value": 0.0,
        "susp_fg": "Y",
        "exchange_tradable": False,
    }
    _write_market_state_artifact(tmp_path, rows=[row])

    result = DataGateway.from_data_analysts(tmp_path).scan_market_state(
        "2025-01-02", "2025-01-02"
    )

    assert result.loc[0, "amount_state"] == "OBSERVED"
    assert result.loc[0, "authoritative_traded_value"] == 0.0


@pytest.mark.parametrize(
    ("row", "error"),
    [
        (
            {
                **_market_state_rows()[1],
                "amount_state": "OBSERVED",
                "authoritative_traded_value": 0.0,
                "amount_zero_authorized": False,
            },
            "APISTKATTR_SUSPENSION_NO_PRICE.*authority tuple",
        ),
        (
            {
                **_market_state_rows()[1],
                "attr_row_present": False,
                "atten_fg": None,
                "disp_fg": None,
                "full_fg": None,
                "limit_fg": None,
                "limo_fg": None,
                "sbadt_fg": None,
                "ssadt_fg": None,
                "susp_fg": None,
                "full_delivery": None,
                "state_reason": "ACTIVE_LIFECYCLE_DUAL_SOURCE_ABSENCE",
                "amount_state": "OBSERVED",
                "authoritative_traded_value": 0.0,
                "amount_zero_authorized": False,
            },
            "ACTIVE_LIFECYCLE_DUAL_SOURCE_ABSENCE.*authority tuple",
        ),
    ],
)
def test_scan_market_state_rejects_no_price_authority_tuple_mismatch(
    tmp_path: Path,
    row: dict[str, object],
    error: str,
) -> None:
    _write_market_state_artifact(tmp_path, rows=[row])

    with pytest.raises(DataContractError, match=error):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-03"
        )


def test_scan_market_state_rejects_bool_amount_physical_type(tmp_path: Path) -> None:
    path = _write_market_state_artifact(tmp_path)
    _replace_market_state_column(tmp_path, path, "authoritative_traded_value", [True, False], pa.bool_())

    with pytest.raises(DataContractError, match="physical schema"):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-03"
        )


def test_scan_market_state_rejects_nan_for_missing_amount(tmp_path: Path) -> None:
    row = {
        **_market_state_rows()[0],
        "market_state": "MISSING",
        "state_reason": "APIPRCD_INVALID_AMOUNT",
        "amount_state": "MISSING",
        "amount_zero_authorized": False,
        "exchange_tradable": None,
    }
    path = _write_market_state_artifact(tmp_path, rows=[row])
    _replace_market_state_column(
        tmp_path,
        path,
        "authoritative_traded_value",
        [float("nan")],
        pa.float64(),
    )

    with pytest.raises(DataContractError, match="true null"):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-02"
        )


def test_scan_market_state_accepts_index_price_row_only_contract(tmp_path: Path) -> None:
    row = {
        **_market_state_rows()[0],
        "ticker": "IX0001",
        "market": "INDEX",
        "instrument_kind": "INDEX",
        "identity_source": "APIPRCD_PRICE_ROW",
        "security_master_market": None,
        "lifecycle_list_date": None,
        "lifecycle_delist_date": None,
        "lifecycle_interval_start": None,
        "lifecycle_interval_end_exclusive": None,
        "lifecycle_active": False,
        "attr_row_present": False,
        "atten_fg": None,
        "disp_fg": None,
        "full_fg": None,
        "limit_fg": None,
        "limo_fg": None,
        "sbadt_fg": None,
        "ssadt_fg": None,
        "susp_fg": None,
        "full_delivery": None,
    }
    _write_market_state_artifact(tmp_path, rows=[row])

    result = DataGateway.from_data_analysts(tmp_path).scan_market_state(
        "2025-01-02", "2025-01-02"
    )

    assert result.loc[0, "identity_source"] == "APIPRCD_PRICE_ROW"


@pytest.mark.parametrize(
    ("row", "error"),
    [
        (
            {**_market_state_rows()[0], "lifecycle_delist_date": "2025-01-02"},
            "delist",
        ),
        (
            {
                **_market_state_rows()[0],
                "lifecycle_list_date": "2025-01-03",
                "lifecycle_interval_start": "2025-01-03",
            },
            "interval",
        ),
        ({**_market_state_rows()[0], "security_master_market": "TPEX"}, "market"),
        (
            {
                **_market_state_rows()[0],
                "instrument_kind": "INDEX",
                "identity_source": "APIPRCD_PRICE_ROW",
                "security_master_market": None,
                "lifecycle_list_date": None,
                "lifecycle_delist_date": None,
                "lifecycle_interval_start": None,
                "lifecycle_interval_end_exclusive": None,
                "lifecycle_active": False,
                "price_row_present": False,
                "market_state": "MISSING",
                "state_reason": "NO_AUTHORIZED_STATE_KEY",
                "amount_state": "MISSING",
                "authoritative_traded_value": None,
                "amount_zero_authorized": False,
                "exchange_tradable": None,
            },
            "index.*price",
        ),
    ],
)
def test_scan_market_state_rejects_invalid_lifecycle_contract(
    tmp_path: Path,
    row: dict[str, object],
    error: str,
) -> None:
    _write_market_state_artifact(tmp_path, rows=[row])

    with pytest.raises(DataContractError, match=error):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-02"
        )


@pytest.mark.parametrize(
    ("row", "error"),
    [
        (
            {**_market_state_rows()[0], "lifecycle_interval_start": "2000-01-01"},
            "interval_start",
        ),
        (
            {**_market_state_rows()[0], "lifecycle_interval_end_exclusive": "2025-01-06"},
            "interval_end",
        ),
        (
            {
                **_market_state_rows()[0],
                "ticker": "IX0001",
                "market": "NASDAQ",
                "instrument_kind": "INDEX",
                "identity_source": "APIPRCD_PRICE_ROW",
                "security_master_market": None,
                "lifecycle_list_date": None,
                "lifecycle_delist_date": None,
                "lifecycle_interval_start": None,
                "lifecycle_interval_end_exclusive": None,
                "lifecycle_active": False,
            },
            "index market",
        ),
    ],
)
def test_scan_market_state_binds_lifecycle_interval_and_index_market_to_manifest_bounds(
    tmp_path: Path,
    row: dict[str, object],
    error: str,
) -> None:
    _write_market_state_artifact(tmp_path, rows=[row])

    with pytest.raises(DataContractError, match=error):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-02"
        )


@pytest.mark.parametrize(
    ("row", "error"),
    [
        ({**_market_state_rows()[0], "observation_date": "2025-01-01"}, "observation_date"),
        ({**_market_state_rows()[0], "source_available_date": "2025-01-01"}, "source_available_date"),
        ({**_market_state_rows()[0], "earliest_execution_session": "2025-01-02"}, "earliest_execution_session"),
        ({**_market_state_rows()[0], "availability_precision": "INTRADAY"}, "availability_precision"),
        ({**_market_state_rows()[0], "classification_policy_version": "old-policy"}, "classification_policy_version"),
    ],
)
def test_scan_market_state_rejects_non_pit_availability_or_policy(
    tmp_path: Path,
    row: dict[str, object],
    error: str,
) -> None:
    _write_market_state_artifact(tmp_path, rows=[row])

    with pytest.raises(DataContractError, match=error):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-02"
        )


@pytest.mark.parametrize(
    ("rows", "manifest_overrides", "error"),
    [
        (None, {"status": "failed"}, "status.*failed"),
        (None, {"build_start": "2025-01-03"}, "coverage"),
        (
            [{**_market_state_rows()[0], "market_state": "DELISTED"}],
            None,
            "market_state",
        ),
        (
            [{**_market_state_rows()[0], "market_state": "UNKNOWN"}],
            None,
            "market_state",
        ),
        (
            [{**_market_state_rows()[0], "amount_state": "UNKNOWN"}],
            None,
            "amount_state",
        ),
        (
            [
                {
                    **_market_state_rows()[1],
                    "authoritative_traded_value": 1.0,
                }
            ],
            None,
            "ZERO_AUTHORIZED",
        ),
        (
            [
                {
                    **_market_state_rows()[0],
                    "market_state": "MISSING",
                    "amount_state": "MISSING",
                    "authoritative_traded_value": 1.0,
                    "amount_zero_authorized": False,
                    "exchange_tradable": None,
                }
            ],
            None,
            "MISSING",
        ),
        (
            [{**_market_state_rows()[0], "lifecycle_pit_status": None}],
            None,
            "lifecycle_pit_status",
        ),
        (
            [{**_market_state_rows()[0], "price_manifest_sha256": None}],
            None,
            "price_manifest_sha256",
        ),
        (
            None,
            {"dependency_manifest_sha256_by_contract": {}},
            "dependency_manifest_sha256_by_contract",
        ),
    ],
)
def test_scan_market_state_rejects_untrusted_manifest_or_rows(
    tmp_path: Path,
    rows: list[dict[str, object]] | None,
    manifest_overrides: dict[str, object] | None,
    error: str,
) -> None:
    _write_market_state_artifact(
        tmp_path,
        rows=rows,
        manifest_overrides=manifest_overrides,
    )

    with pytest.raises(DataContractError, match=error):
        DataGateway.from_data_analysts(tmp_path).scan_market_state(
            "2025-01-02", "2025-01-03"
        )


def test_scan_market_state_delegates_to_predicate_scanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_market_state_artifact(tmp_path)
    calls: list[tuple[str, dict[str, object]]] = []
    original = DataGateway.scan_artifact

    def scan_spy(
        self: DataGateway,
        artifact_id: str,
        **kwargs: object,
    ) -> pd.DataFrame:
        calls.append((artifact_id, kwargs))
        return original(self, artifact_id, **kwargs)

    monkeypatch.setattr(DataGateway, "scan_artifact", scan_spy)

    result = DataGateway.from_data_analysts(tmp_path).scan_market_state(
        "2025-01-02", "2025-01-03", ["1101"]
    )

    assert result["ticker"].tolist() == ["1101", "1101"]
    assert len(calls) == 1
    artifact_id, arguments = calls[0]
    assert artifact_id == "daily_market_state"
    assert arguments["columns"] == _MARKET_STATE_COLUMNS
    assert arguments["filters"] == (("ticker", "in", ("1101",)),)
    assert arguments["start"] == "2025-01-02"
    assert arguments["end"] == "2025-01-03"
    assert arguments["date_column"] == "date"
    assert arguments["validate_coverage"] is False


def test_trading_calendar_returns_only_twse_trading_days_by_month() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2025-01-30", "2025-01-31", "2025-02-01", "2025-02-03"],
            "market": ["TWSE"] * 4,
            "is_trading_day": [True, False, False, True],
        }
    )
    calendar = TradingCalendar(frame)

    assert calendar.month("2025-01-15") == (pd.Timestamp("2025-01-30"),)
    assert calendar.month("2025-02-15") == (pd.Timestamp("2025-02-03"),)
    assert calendar.month_end("2025-01-15") == pd.Timestamp("2025-01-30")


def test_trading_calendar_rejects_duplicate_twse_dates() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2025-01-02", "2025-01-02"],
            "market": ["TWSE", "TWSE"],
            "is_trading_day": [True, True],
        }
    )

    with pytest.raises(CalendarContractError, match="duplicate.*2025-01-02"):
        TradingCalendar(frame)
