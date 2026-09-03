from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from etf_tricks import ETFTrickResult
from etf_tricks.afml import AFMLBoundaries, AFMLConfig
from etf_tricks.afml.pit import PITDailyInputs, PITSourceAdapter
from etf_tricks.data_gateway import DataGateway


def _publish(
    root: Path,
    artifact_id: str,
    frame: pd.DataFrame,
    logical_key: tuple[str, ...],
    *,
    extra_manifest: dict[str, object] | None = None,
) -> dict[str, object]:
    store = root / "data_store"
    relative = f"canonical/{artifact_id}.parquet"
    path = store / relative
    manifest_dir = store / "manifests"
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    dates = pd.to_datetime(frame["date"])
    manifest: dict[str, object] = {
        "artifact_id": artifact_id,
        "artifact_paths": [relative],
        "columns": frame.columns.tolist(),
        "date_range": [str(dates.min().date()), str(dates.max().date())],
        "status": "ready",
        "row_count": len(frame),
        "duplicate_count": 0,
        "logical_key": list(logical_key),
    }
    manifest.update(extra_manifest or {})
    (manifest_dir / f"{artifact_id}.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return manifest


def _upstream_manifest_hash(manifest: dict[str, object]) -> str:
    payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class PITFixture:
    base: ETFTrickResult
    adapter: PITSourceAdapter
    boundaries: AFMLBoundaries
    inputs: PITDailyInputs


@pytest.fixture
def pit_fixture(tmp_path: Path) -> PITFixture:
    dates = pd.to_datetime(
        [
            "2023-12-29",
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
        ]
    )
    daily_market = pd.DataFrame(
        {
            "date": dates,
            "ticker": "IX0001",
            "close": [99.0, 100.0, 101.0, 100.5, 102.0, 103.0],
            "traded_value": [950.0, 1_000.0, 1_100.0, 900.0, 1_200.0, 1_300.0],
            "source_available_date": dates,
        }
    )
    market_manifest = _publish(
        tmp_path,
        "daily_price_volume",
        daily_market,
        ("date", "ticker"),
        extra_manifest={
            "pit_policy": "source_date_lagged_to_decision_date",
            "availability_field": None,
            "revision_policy": None,
        },
    )
    calendar = pd.DataFrame(
        {
            "date": dates,
            "market": "TWSE",
            "is_trading_day": True,
            "source_available_date": dates,
        }
    )
    calendar_manifest = _publish(
        tmp_path,
        "trading_calendar",
        calendar,
        ("date", "market"),
        extra_manifest={"pit_policy": "source_available_date"},
    )

    daily_etf = pd.DataFrame(
        {
            "date": dates,
            "etf_id": "momentum",
            "nav": [99.0, 100.0, 101.0, 100.5, 102.0, 103.0],
            "daily_return": [0.0, 0.010101, 0.01, -0.0049505, 0.0149254, 0.0098039],
            "etf_amount": [95.0, 100.0, 110.0, 90.0, 120.0, 130.0],
            "missing_traded_value_count": 0,
            "has_data_quality_flag": False,
            "cash_weight": 0.0,
            "invested_weight": 1.0,
            "holdings_count": 5,
            "target_completion_ratio": 1.0,
        }
    )
    base = ETFTrickResult(
        daily_etf=daily_etf,
        daily_holdings=pd.DataFrame(),
        trades=pd.DataFrame(),
        monthly_targets=pd.DataFrame(),
        candidate_audit=pd.DataFrame(),
        diagnostics=pd.DataFrame(),
        metadata={
            "run_config": {
                "start_date": "2024-01-02",
                "end_date": "2024-01-08",
                "initial_capital": "10000000",
            },
            "manifest_hashes": {
                "daily_price_volume": _upstream_manifest_hash(market_manifest),
                "trading_calendar": _upstream_manifest_hash(calendar_manifest),
            },
            "spec_hash": "fixture-spec",
        },
    )
    boundaries = AFMLBoundaries(
        train_start="2024-01-02",
        train_end="2024-01-03",
        validation_end="2024-01-05",
        test_end="2024-01-08",
    )
    adapter = PITSourceAdapter(DataGateway.from_data_analysts(tmp_path))
    inputs = adapter.prepare(base, boundaries, AFMLConfig())
    return PITFixture(base=base, adapter=adapter, boundaries=boundaries, inputs=inputs)
