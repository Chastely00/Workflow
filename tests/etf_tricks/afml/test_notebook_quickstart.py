from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from etf_tricks import ETFTrickResult
from etf_tricks.result import append_lifecycle_evidence
from etf_tricks.afml import AFMLDataset, AFML_TABLE_NAMES


NOTEBOOK = Path("ETF_Tricks_AFML_Quickstart.ipynb")


def _runtime_dataset() -> AFMLDataset:
    date = pd.Timestamp("2025-01-10")
    available = pd.Timestamp("2025-01-10 23:00:00", tz="Asia/Taipei")
    tables = {name: pd.DataFrame() for name in AFML_TABLE_NAMES}
    tables.update(
        {
            "source_capabilities": pd.DataFrame(
                {"feature_id": ["IX0001"], "status": ["AVAILABLE"]}
            ),
            "dollar_bars": pd.DataFrame(
                {
                    "etf_id": ["momentum"],
                    "bar_id": [1],
                    "bar_status": ["FINALIZED"],
                    "bar_role": ["LIVE_ELIGIBLE"],
                    "bar_end_date": [date],
                    "feature_available_at": [available],
                    "bar_available_at": [available],
                    "live_eligible": [True],
                    "calibration_effective_at": [
                        pd.Timestamp("2025-01-02", tz="Asia/Taipei")
                    ],
                    "source_quality_flag": [False],
                }
            ),
            "open_bar_checkpoints": pd.DataFrame(
                columns=["etf_id", "bar_id", "bar_status"]
            ),
            "bar_daily_membership": pd.DataFrame(
                {
                    "etf_id": ["momentum"],
                    "bar_id": [1],
                    "date": [date],
                    "observation_date": [date],
                    "source_available_at": [available],
                    "ix0001_source_available_at": [available],
                    "member_available_at": [available],
                    "ingested_at": [pd.NaT],
                    "source_revision_id": [pd.NA],
                    "source_manifest_hash": ["fixture-hash"],
                    "ix0001_ingested_at": [pd.NaT],
                    "ix0001_source_revision_id": [pd.NA],
                    "ix0001_source_manifest_hash": ["fixture-hash"],
                }
            ),
            "ffd_weights": pd.DataFrame(
                {
                    "etf_id": ["momentum"],
                    "calibration_version": ["ffd-v1"],
                    "weight_lag": [0],
                }
            ),
            "ffd_search": pd.DataFrame(
                {"etf_id": ["momentum"], "search_order": [0]}
            ),
            "ffd_series": pd.DataFrame(
                {"etf_id": ["momentum"], "bar_id": [1], "ffd_level": [0.1]}
            ),
            "structural_features": pd.DataFrame(
                {"entity_id": ["momentum"], "observation_id": ["1"]}
            ),
            "features": pd.DataFrame(
                {
                    "etf_id": ["momentum"],
                    "bar_id": [1],
                    "bar_end_date": [date],
                    "feature_available_at": [available],
                    "bar_amount": [100.0],
                    "ffd_level": [0.1],
                    "ffd_missing": [False],
                    "ix_alignment_reason": [None],
                    "source_quality_flag": [False],
                }
            ),
            "events": pd.DataFrame(
                {
                    "etf_id": ["momentum"],
                    "event_id": ["momentum-1"],
                    "t0_bar_id": [1],
                    "t0_observation_date": [date],
                    "event_available_at": [available],
                }
            ),
            "labels": pd.DataFrame(
                {
                    "event_id": ["momentum-1"],
                    "label": [1],
                    "label_status": ["resolved"],
                    "eligible_for_train": [True],
                    "eligible_for_validation": [False],
                    "eligible_for_test": [False],
                    "t1": [date],
                    "label_available_at": [available],
                }
            ),
            "diagnostics": pd.DataFrame(
                {"stage": ["fixture"], "code": ["OK"]}
            ),
        }
    )
    return AFMLDataset(
        **tables,
        metadata={
            "schema_version": "etf-afml-dataset-v2",
            "config_sha256": "notebook-fixture",
            "etf_ids": ["momentum"],
            "train_decision_cutoff": "2025-01-15T23:59:59+08:00",
            "validation_decision_cutoff": "2025-01-20T23:59:59+08:00",
            "test_decision_cutoff": "2025-01-31T23:59:59+08:00",
            "trading_sessions": ["2025-01-10", "2025-01-13"],
        },
        readiness={
            "status": "CORE_READY_FOR_BOUNDED_RESEARCH",
            "core_ready": True,
            "finalized": False,
        },
    )


def test_quickstart_is_output_free_and_uses_public_api():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    source = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )

    assert "from etf_tricks.afml import AFMLConfig, ETFAFMLLab" in source
    assert "ETFTrickResult.read" in source
    assert "ETF_TRICK_RESULT_MANIFEST_SHA256" in source
    assert "ETF_TRICK_MARKET_STATE_IDENTITY_SHA256" in source
    assert "expected_handle=result_handle" in source
    assert "dataset.for_ml" in source
    assert "dataset.for_trading" in source
    assert all(
        not cell.get("outputs")
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )
    assert all(
        cell.get("execution_count") is None
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )


def test_quickstart_code_cells_execute_against_synthetic_artifacts(
    tmp_path, monkeypatch
):
    result_dir = tmp_path / "result"
    dataset_dir = tmp_path / "afml"
    dates = pd.to_datetime(["2025-01-10"])
    lifecycle = {
        "state_row_count": 0,
        "lifecycle_active_row_count": 0,
        "lifecycle_inactive_row_count": 0,
        "lifecycle_conflict_count": 0,
        "identity_conflict_count": 0,
        "formation_state_counts": {},
        "formation_exclusion_reason_counts": {},
    }
    result_handle = ETFTrickResult(
        daily_etf=pd.DataFrame(
            {
                "date": dates,
                "etf_id": "momentum",
                "nav": 100.0,
                "daily_return": 0.0,
                "etf_amount": 100.0,
            }
        ),
        daily_holdings=pd.DataFrame(),
        trades=pd.DataFrame(),
        monthly_targets=pd.DataFrame(),
        candidate_audit=pd.DataFrame(),
        diagnostics=append_lifecycle_evidence(pd.DataFrame(), lifecycle),
        metadata={
            "run_config": {},
            "manifest_hashes": {
                artifact: "a" * 64
                for artifact in {
                    "trading_calendar",
                    "daily_price_volume",
                    "daily_chip",
                    "monthly_sales",
                    "financial_statement_raw",
                    "security_master",
                    "daily_market_state",
                }
            },
            "spec_hash": "b" * 64,
            "market_state_identity": {
                "artifact_id": "daily_market_state",
                "active_version": "market-state-v3",
                "classification_policy_version": "daily_market_state_v3",
                "state_lattice_policy_version": "daily_market_state_lattice_v5",
                "market_identity_policy_version": "daily_market_identity_v3",
                "dependency_certification_fingerprint": "b" * 64,
                "manifest_sha256": "a" * 64,
            },
            "market_state_config": {
                "formation_admission": "TRADING_ONLY",
                "execution_admission": (
                    "SAME_SESSION_TRADING_AND_EXCHANGE_TRADABLE"
                ),
                "amount_source": (
                    "PRIOR_SESSION_HOLDINGS_AUTHORITATIVE_TRADED_VALUE"
                ),
                "scan_start_date": "2025-01-10",
                "scan_end_date": "2025-01-10",
            },
            "lifecycle_diagnostics": lifecycle,
        },
    ).write(result_dir)
    _runtime_dataset().write(dataset_dir)
    monkeypatch.setenv("ETF_TRICK_RESULT_DIR", str(result_dir))
    monkeypatch.setenv(
        "ETF_TRICK_RESULT_MANIFEST_SHA256", result_handle.manifest_sha256
    )
    monkeypatch.setenv(
        "ETF_TRICK_MARKET_STATE_IDENTITY_SHA256",
        result_handle.market_state_identity_sha256,
    )
    monkeypatch.setenv("ETF_AFML_DATASET_DIR", str(dataset_dir))
    monkeypatch.setenv("DATA_ANALYSTS_ROOT", str(tmp_path / "unused-data-root"))
    monkeypatch.setenv("ETF_AFML_AS_OF", "2025-01-10")

    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    namespace = {"__name__": "__notebook_test__"}
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            exec("".join(cell["source"]), namespace)

    assert isinstance(namespace["dataset"], AFMLDataset)
    assert namespace["ml_train"].iloc[0]["label"] == 1
    assert namespace["trading_snapshot"].iloc[0]["availability_status"] == "AVAILABLE"
