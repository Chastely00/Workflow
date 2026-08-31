from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
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
            "lifecycle_interval_start": "2000-01-01",
            "lifecycle_interval_end_exclusive": "2025-01-07",
            "lifecycle_active": True,
            "lifecycle_conflict": False,
            "identity_conflict": False,
            "lifecycle_pit_status": "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED",
            "revision_pit_status": "PIT_REVISION_UNVERIFIED",
            "observation_date": "2025-01-02",
            "source_available_date": "2025-01-02",
            "availability_precision": "AFTER_CLOSE",
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
            "lifecycle_interval_start": "2000-01-01",
            "lifecycle_interval_end_exclusive": "2025-01-07",
            "lifecycle_active": True,
            "lifecycle_conflict": False,
            "identity_conflict": False,
            "lifecycle_pit_status": "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED",
            "revision_pit_status": "PIT_REVISION_UNVERIFIED",
            "observation_date": "2025-01-03",
            "source_available_date": "2025-01-03",
            "availability_precision": "AFTER_CLOSE",
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
    manifest = {
        "artifact_id": "daily_market_state",
        "artifact_paths": [artifact_path],
        "columns": list(_MARKET_STATE_COLUMNS),
        "date_range": list(date_range) if date_range else None,
        "availability_date_range": list(date_range) if date_range else None,
        "status": status,
        "row_count": len(frame),
        "duplicate_count": 0,
        "logical_key": ["date", "ticker"],
        "dependency_manifest_sha256_by_contract": {
            "security_master": _SHA256,
            "trading_calendar": _SHA256,
            "daily_price_volume": _SHA256,
            "daily_tradability": _SHA256,
        },
    }
    manifest.update(manifest_overrides or {})
    (manifest_dir / "daily_market_state.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return parquet_path


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


@pytest.mark.parametrize(
    ("rows", "manifest_overrides", "error"),
    [
        (None, {"status": "failed"}, "status.*failed"),
        (None, {"date_range": ["2025-01-03", "2025-01-03"]}, "coverage"),
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
    assert calls == [
        (
            "daily_market_state",
            {
                "columns": _MARKET_STATE_COLUMNS,
                "filters": (("ticker", "in", ("1101",)),),
                "start": "2025-01-02",
                "end": "2025-01-03",
                "date_column": "date",
            },
        )
    ]


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
