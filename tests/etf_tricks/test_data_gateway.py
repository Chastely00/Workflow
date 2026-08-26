from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from etf_tricks.calendar import CalendarContractError, TradingCalendar
from etf_tricks.data_gateway import DataContractError, DataGateway


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
