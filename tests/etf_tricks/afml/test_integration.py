from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etf_tricks import ETFAFMLLab, ETFTrickResult
from etf_tricks.afml import (
    AFMLConfig,
    AFMLScopeError,
    DollarBarConfig,
    FFDConfig,
    FeatureConfig,
    LabelConfig,
    StructuralConfig,
)


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


def _manifest_hash(manifest: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


@pytest.fixture(scope="module")
def bounded_inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("afml-data")
    dates = pd.bdate_range("2024-01-02", "2026-07-07")
    future_dates = pd.bdate_range("2024-01-02", "2026-07-15")
    ix_close = 20_000.0 * np.exp(np.linspace(0, 0.18, len(dates)))
    ix_amount = 1_000_000_000.0 * (
        1.0 + 0.08 * np.sin(np.arange(len(dates)) / 17.0)
    )
    market = pd.DataFrame(
        {
            "date": dates,
            "ticker": "IX0001",
            "close": ix_close,
            "traded_value": ix_amount,
        }
    )
    market_manifest = _publish(
        root,
        "daily_price_volume",
        market,
        ("date", "ticker"),
        extra_manifest={
            "pit_policy": "source_date_lagged_to_decision_date",
            "availability_field": None,
            "revision_policy": None,
        },
    )
    calendar = pd.DataFrame(
        {
            "date": future_dates,
            "market": "TWSE",
            "is_trading_day": True,
        }
    )
    calendar_manifest = _publish(
        root,
        "trading_calendar",
        calendar,
        ("date", "market"),
        extra_manifest={"pit_policy": "source_available_date"},
    )

    rows = []
    holding_rows = []
    for etf_index, etf_id in enumerate(("momentum", "low_volatility")):
        oscillation = 0.012 * np.sin(np.arange(len(dates)) / (8.0 + etf_index))
        log_nav = np.linspace(0, 0.22 - etf_index * 0.05, len(dates)) + oscillation
        nav = 100.0 * np.exp(log_nav)
        amounts = (1_400_000.0 + etf_index * 200_000.0) * (
            1.0 + 0.25 * np.sin(np.arange(len(dates)) / 11.0 + etf_index)
        )
        for position, date in enumerate(dates):
            rows.append(
                {
                    "date": date,
                    "etf_id": etf_id,
                    "nav": nav[position],
                    "daily_return": (
                        np.nan if position == 0 else nav[position] / nav[position - 1] - 1
                    ),
                    "etf_amount": amounts[position],
                    "missing_traded_value_count": 0,
                    "has_data_quality_flag": False,
                    "cash_weight": 0.02,
                    "invested_weight": 0.98,
                    "holdings_count": 2,
                    "target_completion_ratio": 1.0,
                }
            )
            holding_rows.extend(
                [
                    {
                        "date": date,
                        "etf_id": etf_id,
                        "ticker": "1101",
                        "actual_weight": 0.58,
                    },
                    {
                        "date": date,
                        "etf_id": etf_id,
                        "ticker": "2330",
                        "actual_weight": 0.40,
                    },
                ]
            )
    base = ETFTrickResult(
        daily_etf=pd.DataFrame(rows),
        daily_holdings=pd.DataFrame(holding_rows),
        trades=pd.DataFrame(),
        monthly_targets=pd.DataFrame(),
        candidate_audit=pd.DataFrame(),
        diagnostics=pd.DataFrame(),
        metadata={
            "spec_hash": "bounded-integration-fixture",
            "run_config": {
                "start_date": "2024-01-02",
                "end_date": "2026-07-07",
                "initial_capital": "10000000",
            },
            "manifest_hashes": {
                "daily_price_volume": _manifest_hash(market_manifest),
                "trading_calendar": _manifest_hash(calendar_manifest),
            },
        },
    )
    config = AFMLConfig(
        dollar_bar=DollarBarConfig(
            market_amount_lookback_days=20,
            min_market_amount_observations=10,
            candidate_quantile_count=25,
            min_completed_bars=30,
            max_bar_duration_trading_days=20,
        ),
        ffd=FFDConfig(weight_tolerance=0.01, min_adf_observations=30),
        structural=StructuralConfig(min_sample_length=30, q=0.9, v=0.05),
        features=FeatureConfig(
            ffd_ma_window=10,
            ffd_vol_windows=(5, 10),
            shape_window=20,
            min_shape_obs=10,
            amount_window=10,
            efficiency_window=10,
            market_vol_windows=(10, 20),
            beta_window=20,
        ),
        labels=LabelConfig(
            volatility_span=20,
            min_obs=10,
            vertical_bars=20,
        ),
    )
    return root, base, config


def _build(lab, base, config, **updates):
    values = {
        "config": config,
        "mode": "train",
        "train_start": "2024-01-02",
        "train_end": "2025-03-31",
        "validation_end": "2025-12-31",
        "test_end": "2026-07-07",
        "etf_ids": ("momentum", "low_volatility"),
    }
    values.update(updates)
    return lab.build_all(base, **values)


def test_build_all_returns_importable_tables_for_two_etfs(bounded_inputs):
    root, base, config = bounded_inputs
    dataset = _build(ETFAFMLLab.from_data_analysts(root), base, config)

    assert set(dataset.dollar_bars["etf_id"]) == {
        "momentum",
        "low_volatility",
    }
    assert dataset.features["bar_amount"].notna().all()
    assert dataset.metadata["scope"] == "BOUNDED_TEST"
    assert dataset.metadata["test_end"] == "2026-07-07"
    assert dataset.diagnostics["elapsed_seconds"].ge(0).all()


def test_full_history_13_etf_scope_requires_explicit_acceptance(bounded_inputs):
    root, base, config = bounded_inputs
    with pytest.raises(AFMLScopeError, match="full_history_acceptance"):
        ETFAFMLLab.from_data_analysts(root).build_all(
            base,
            config=config,
            mode="train",
            train_start="2005-01-03",
            train_end="2020-12-31",
            validation_end="2023-12-31",
            test_end="2026-07-07",
        )


def test_research_full_history_is_descriptive_only(bounded_inputs):
    root, base, config = bounded_inputs
    dataset = _build(
        ETFAFMLLab.from_data_analysts(root),
        base,
        config,
        mode="research_full_history",
        etf_ids=("momentum",),
    )

    assert dataset.metadata["readiness_scope"] == "DESCRIPTIVE_ONLY"
    with pytest.raises(AFMLScopeError, match="DESCRIPTIVE_ONLY"):
        dataset.for_ml("momentum", split="test")


def test_walk_forward_versions_do_not_recut_open_bars(bounded_inputs):
    root, base, config = bounded_inputs
    dataset = _build(
        ETFAFMLLab.from_data_analysts(root),
        base,
        config,
        mode="walk_forward",
        etf_ids=("momentum",),
        retrain_dates=("2025-07-01", "2026-01-02"),
    )

    bars = dataset.dollar_bars.sort_values("bar_start_date")
    assert bars["calibration_version"].nunique() >= 2
    assert bars.groupby(["etf_id", "bar_id"])["calibration_version"].nunique().max() == 1
    assert not bars.duplicated(["etf_id", "bar_id"]).any()


def test_production_feature_schema_applies_live_gates_on_effective_date(
    bounded_inputs,
):
    root, base, config = bounded_inputs
    dataset = _build(ETFAFMLLab.from_data_analysts(root), base, config)
    effective = pd.Timestamp(
        dataset.metadata["calibrations"][0]["calibration_effective_at"]
    )

    snapshot = dataset.for_trading(
        as_of=effective.tz_localize(None), decision_cutoff="after_close"
    )

    assert not any(column.endswith(("_x", "_y")) for column in snapshot.columns)
    selected = snapshot[snapshot["snapshot_status"].eq("AVAILABLE")]
    if not selected.empty:
        assert selected["bar_role"].eq("LIVE_ELIGIBLE").all()
        assert selected["live_eligible"].eq(True).all()
        assert selected["calibration_effective_at"].notna().all()
