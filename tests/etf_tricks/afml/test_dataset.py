from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etf_tricks.afml import AFMLDataset, AFML_TABLE_NAMES


def _empty() -> pd.DataFrame:
    return pd.DataFrame()


@pytest.fixture
def dataset_fixture() -> AFMLDataset:
    dates = pd.to_datetime(["2025-01-10", "2025-01-20", "2025-01-30"])
    available = dates.tz_localize("Asia/Taipei") + pd.Timedelta(hours=23)
    features = pd.DataFrame(
        {
            "etf_id": "momentum",
            "bar_id": [1, 2, 3],
            "bar_end_date": dates,
            "feature_available_at": available,
            "bar_amount": [100.0, 120.0, 140.0],
            "ffd_level": [0.1, 0.2, 0.3],
            "ffd_missing": False,
            "structural_etf_missing": False,
            "ix_missing": False,
            "ix_alignment_reason": None,
            "calibration_version": "q-v1",
            "source_quality_flag": False,
        }
    )
    bars = pd.DataFrame(
        {
            "etf_id": "momentum",
            "bar_id": [1, 2, 3],
            "bar_status": "FINALIZED",
            "bar_role": "LIVE_ELIGIBLE",
            "bar_end_date": dates,
            "feature_available_at": available,
            "bar_available_at": available,
            "live_eligible": True,
            "calibration_effective_at": pd.Timestamp(
                "2025-01-05 23:59:59", tz="Asia/Taipei"
            ),
            "calibration_version": "q-v1",
            "source_quality_flag": False,
        }
    )
    events = pd.DataFrame(
        {
            "etf_id": "momentum",
            "event_id": ["momentum-1", "momentum-2", "momentum-3"],
            "t0_bar_id": [1, 2, 3],
            "t0_observation_date": dates,
            "event_available_at": available,
            "average_uniqueness": [1.0, 0.5, 0.5],
        }
    )
    labels = pd.DataFrame(
        {
            "etf_id": "momentum",
            "event_id": ["momentum-1", "momentum-2", "momentum-3"],
            "t0_bar_id": [1, 2, 3],
            "t1": dates + pd.Timedelta(days=2),
            "label_available_at": available + pd.Timedelta(days=2),
            "first_touch_date": dates + pd.Timedelta(days=2),
            "label": [1, -1, 1],
            "label_status": "resolved",
            "eligible_for_train": [True, False, False],
            "eligible_for_validation": [False, True, False],
            "eligible_for_test": [False, False, True],
        }
    )
    sessions = pd.bdate_range("2025-01-02", "2025-02-10").strftime("%Y-%m-%d").tolist()
    metadata = {
        "schema_version": "etf-afml-dataset-v1",
        "config_sha256": "fixture-config-sha",
        "etf_ids": ["momentum"],
        "train_start": "2025-01-01",
        "train_end": "2025-01-15",
        "validation_end": "2025-01-25",
        "test_end": "2025-01-31",
        "train_decision_cutoff": "2025-01-15T23:59:59+08:00",
        "validation_decision_cutoff": "2025-01-25T23:59:59+08:00",
        "test_decision_cutoff": "2025-01-31T23:59:59+08:00",
        "trading_sessions": sessions,
        "calibration_scope": "train_only",
    }
    return AFMLDataset(
        source_capabilities=pd.DataFrame(
            {"feature_id": ["IX0001"], "status": ["AVAILABLE"]}
        ),
        dollar_bars=bars,
        open_bar_checkpoints=pd.DataFrame(
            columns=["etf_id", "bar_id", "bar_status"]
        ),
        bar_daily_membership=pd.DataFrame(
            {
                "etf_id": "momentum",
                "bar_id": [1, 2, 3],
                "date": dates,
                "nav": [100.0, 101.0, 102.0],
            }
        ),
        ffd_weights=pd.DataFrame(
            {
                "etf_id": "momentum",
                "calibration_version": "ffd-v1",
                "weight_lag": [0, 1, 2],
                "weight": [1.0, -0.3, -0.1],
            }
        ),
        ffd_search=pd.DataFrame(
            {
                "etf_id": "momentum",
                "search_order": [0],
                "d": [0.3],
                "passed": [True],
            }
        ),
        ffd_series=features[["etf_id", "bar_id", "ffd_level"]],
        structural_features=pd.DataFrame(
            {
                "entity_id": ["momentum"],
                "observation_id": [3],
                "sadf": [1.2],
            }
        ),
        features=features,
        events=events,
        labels=labels,
        diagnostics=pd.DataFrame(
            {"stage": ["fixture"], "code": ["OK"], "severity": ["INFO"]}
        ),
        metadata=metadata,
        readiness={"status": "READY_FOR_BOUNDED_RESEARCH"},
    )


def test_afml_dataset_round_trip_verifies_all_table_hashes(
    tmp_path: Path, dataset_fixture: AFMLDataset
):
    manifest = dataset_fixture.write(tmp_path / "afml-run")

    assert set(manifest["tables"]) == set(AFML_TABLE_NAMES)
    restored = AFMLDataset.read(tmp_path / "afml-run")
    pd.testing.assert_frame_equal(restored.features, dataset_fixture.features)
    assert restored.metadata["config_sha256"] == dataset_fixture.metadata[
        "config_sha256"
    ]


def test_for_trading_cannot_expose_labels_or_future_rows(dataset_fixture):
    snapshot = dataset_fixture.for_trading(
        as_of="2025-01-31", decision_cutoff="after_close"
    )

    forbidden = {"label", "t1", "label_available_at", "first_touch_date"}
    assert forbidden.isdisjoint(snapshot.columns)
    assert snapshot["feature_available_at"].le(snapshot["decision_time"]).all()
    assert snapshot["live_eligible"].all()
    assert snapshot.iloc[0]["earliest_execution_session"] == pd.Timestamp(
        "2025-02-03"
    )


def test_split_views_use_feature_and_label_availability(dataset_fixture):
    assert dataset_fixture.train["feature_available_at"].le(
        pd.Timestamp(dataset_fixture.metadata["train_decision_cutoff"])
    ).all()
    labelled = dataset_fixture.train.dropna(subset=["label"])
    assert labelled["label_available_at"].le(
        pd.Timestamp(dataset_fixture.metadata["train_decision_cutoff"])
    ).all()
    assert len(dataset_fixture.for_ml("momentum", split="validation")) == 1


def test_corrupt_parquet_fails_hash_verification(tmp_path: Path, dataset_fixture):
    output = tmp_path / "afml-run"
    manifest = dataset_fixture.write(output)
    relative = manifest["tables"]["features"]["path"]
    (output / relative).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="SHA-256"):
        AFMLDataset.read(output)


def test_duplicate_canonical_key_fails_closed(dataset_fixture):
    duplicate = pd.concat(
        [dataset_fixture.features, dataset_fixture.features.iloc[[0]]],
        ignore_index=True,
    )
    values = {
        name: getattr(dataset_fixture, name) for name in AFML_TABLE_NAMES
    }
    values["features"] = duplicate

    with pytest.raises(ValueError, match="duplicate"):
        AFMLDataset(
            **values,
            metadata=dataset_fixture.metadata,
            readiness=dataset_fixture.readiness,
        )


def test_pre_calibration_trading_request_is_explicitly_unavailable(dataset_fixture):
    snapshot = dataset_fixture.for_trading(
        as_of="2025-01-03", decision_cutoff="after_close"
    )

    assert len(snapshot) == 1
    assert snapshot.iloc[0]["availability_status"] == "PRE_CALIBRATION"
    assert pd.isna(snapshot.iloc[0]["bar_id"])


def test_manifest_contains_schema_keys_rows_and_metadata_identity(
    tmp_path: Path, dataset_fixture
):
    manifest = dataset_fixture.write(tmp_path / "afml-run")

    assert manifest["metadata_sha256"]
    assert manifest["readiness_sha256"]
    for table in AFML_TABLE_NAMES:
        evidence = manifest["tables"][table]
        assert {"sha256", "row_count", "columns", "key", "path"}.issubset(evidence)
    json.dumps(manifest)
