from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from etf_tricks.afml.capabilities import SourceCapabilityAuditor
from etf_tricks.data_gateway import DataGateway


@pytest.fixture
def capability_root(tmp_path: Path) -> Path:
    relative_path = "canonical/raw/daily_price_volume/year=2025/part.parquet"
    parquet_path = tmp_path / "data_store" / relative_path
    manifest_dir = tmp_path / "data_store" / "manifests"
    parquet_path.parent.mkdir(parents=True)
    manifest_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "date": ["2025-01-02", "2025-01-03", "2025-01-03"],
            "ticker": ["IX0001", "IX0001", "1101"],
            "open": [99.0, 100.0, 10.0],
            "high": [101.0, 102.0, 11.0],
            "low": [98.0, 99.0, 9.0],
            "close": [100.0, 101.0, 10.5],
            "traded_value": [1_000.0, 1_100.0, 50.0],
            "source_available_date": ["2025-01-02", "2025-01-03", "2025-01-03"],
        }
    ).to_parquet(parquet_path, index=False)
    manifest = {
        "artifact_id": "daily_price_volume",
        "artifact_paths": [relative_path],
        "columns": [
            "date",
            "ticker",
            "open",
            "high",
            "low",
            "close",
            "traded_value",
            "source_available_date",
        ],
        "date_range": ["2025-01-02", "2025-01-03"],
        "status": "ready",
        "row_count": 3,
        "duplicate_count": 0,
        "logical_key": ["date", "ticker"],
        "pit_policy": "explicit_source_availability",
        "availability_field": "source_available_date",
        "revision_policy": "append_only_vintages",
    }
    (manifest_dir / "daily_price_volume.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return tmp_path


def test_capability_audit_does_not_invent_unavailable_features(
    capability_root: Path,
) -> None:
    table = SourceCapabilityAuditor(
        DataGateway.from_data_analysts(capability_root)
    ).audit()
    status = table.set_index("feature_id")["status"].to_dict()

    assert status == {
        "IX0001": "AVAILABLE_VERIFIED",
        "VPIN": "UNAVAILABLE_SOURCE_GRAIN",
        "KYLE_LAMBDA": "UNAVAILABLE_SOURCE_GRAIN",
        "ATR": "UNAVAILABLE_SOURCE_GRAIN",
        "ADX": "UNAVAILABLE_SOURCE_GRAIN",
        "VIX": "UNAVAILABLE_SOURCE_GRAIN",
    }
    ix = table.set_index("feature_id").loc["IX0001"]
    assert ix["selected_row_count"] == 2
    assert len(ix["manifest_sha256"]) == 64
    assert len(ix["selected_rows_sha256"]) == 64
    assert ix["revision_status"] == "PIT_REVISION_VERIFIED"


def test_current_snapshot_manifest_is_partial_even_when_ix_rows_exist(
    capability_root: Path,
) -> None:
    manifest_path = (
        capability_root / "data_store" / "manifests" / "daily_price_volume.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["availability_field"] = None
    manifest["revision_policy"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    table = SourceCapabilityAuditor(
        DataGateway.from_data_analysts(capability_root)
    ).audit()
    ix = table.set_index("feature_id").loc["IX0001"]

    assert ix["status"] == "PARTIAL_COVERAGE"
    assert ix["revision_status"] == "PIT_REVISION_UNVERIFIED"
    assert "revision" in ix["reason"].lower()
