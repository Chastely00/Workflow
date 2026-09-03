from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from etf_tricks.afml import AFMLConfig
from etf_tricks.afml.pit import (
    PITContractError,
    next_execution_session,
)


def test_date_only_market_data_is_after_close_and_next_session_executable(
    pit_fixture,
):
    inputs = pit_fixture.inputs
    row = inputs.daily_etf.query("etf_id == 'momentum'").iloc[0]

    assert row["availability_assumption"] == "AFTER_CLOSE_DATE_ONLY"
    assert str(row["source_available_at"].tz) == "Asia/Taipei"
    assert row["source_revision_status"] == "PIT_REVISION_UNVERIFIED"
    execution = next_execution_session(
        inputs.trading_calendar,
        row["source_available_at"],
        decision_cutoff="after_close",
    )
    assert execution == pd.Timestamp("2024-01-03")
    assert execution > row["date"]


def test_bounded_inputs_do_not_load_pre_train_market_or_etf_history(pit_fixture):
    inputs = pit_fixture.inputs
    train_start = pd.Timestamp(pit_fixture.boundaries.train_start)

    assert inputs.daily_etf["date"].min() == train_start
    assert inputs.ix0001["date"].min() == train_start


def test_requested_etf_missing_middle_twse_session_fails_closed(pit_fixture):
    truncated = copy.deepcopy(pit_fixture.base)
    truncated.daily_etf = truncated.daily_etf[
        truncated.daily_etf["date"].ne(pd.Timestamp("2024-01-04"))
    ].reset_index(drop=True)

    with pytest.raises(PITContractError, match="momentum.*2024-01-04"):
        pit_fixture.adapter.prepare(
            truncated,
            pit_fixture.boundaries,
            AFMLConfig(),
            requested_etf_ids=("momentum",),
        )


def test_ix0001_missing_middle_twse_session_fails_closed(pit_fixture, monkeypatch):
    original = pit_fixture.adapter.gateway.scan_artifact

    def missing_ix_session(artifact_id, **kwargs):
        frame = original(artifact_id, **kwargs)
        if artifact_id == "daily_price_volume":
            frame = frame[frame["date"].ne(pd.Timestamp("2024-01-04"))]
        return frame.reset_index(drop=True)

    monkeypatch.setattr(
        pit_fixture.adapter.gateway, "scan_artifact", missing_ix_session
    )

    with pytest.raises(PITContractError, match="IX0001.*2024-01-04"):
        pit_fixture.adapter.prepare(
            pit_fixture.base,
            pit_fixture.boundaries,
            AFMLConfig(),
            requested_etf_ids=("momentum",),
        )


def test_calendar_must_cover_requested_window_end(pit_fixture, monkeypatch):
    original = pit_fixture.adapter.gateway.scan_artifact

    def truncated_calendar(artifact_id, **kwargs):
        frame = original(artifact_id, **kwargs)
        if artifact_id == "trading_calendar":
            frame = frame[frame["date"].lt(pd.Timestamp("2024-01-08"))]
        return frame.reset_index(drop=True)

    monkeypatch.setattr(
        pit_fixture.adapter.gateway, "scan_artifact", truncated_calendar
    )

    with pytest.raises(PITContractError, match="trading_calendar.*test_end"):
        pit_fixture.adapter.prepare(
            pit_fixture.base,
            pit_fixture.boundaries,
            AFMLConfig(),
            requested_etf_ids=("momentum",),
        )


def test_current_manifest_hash_mismatch_fails_closed(pit_fixture):
    stale = copy.deepcopy(pit_fixture.base)
    stale.metadata["manifest_hashes"]["daily_price_volume"] = "stale"

    with pytest.raises(PITContractError, match="daily_price_volume"):
        pit_fixture.adapter.prepare(stale, pit_fixture.boundaries, AFMLConfig())


def test_daily_market_state_uses_byte_bound_authority_hash(pit_fixture, monkeypatch):
    base = copy.deepcopy(pit_fixture.base)
    authority = SimpleNamespace(manifest_sha256="d" * 64)
    monkeypatch.setattr(
        pit_fixture.adapter.gateway,
        "capture_market_state_authority",
        lambda: authority,
    )
    original_load_manifest = pit_fixture.adapter.gateway.load_manifest
    monkeypatch.setattr(
        pit_fixture.adapter.gateway,
        "load_manifest",
        lambda artifact_id: {} if artifact_id == "daily_market_state" else original_load_manifest(artifact_id),
    )
    base.metadata["manifest_hashes"]["daily_market_state"] = authority.manifest_sha256

    inputs = pit_fixture.adapter.prepare(base, pit_fixture.boundaries, AFMLConfig())

    assert (
        inputs.source_identity["manifest_hashes"]["daily_market_state"]
        == authority.manifest_sha256
    )


def test_flagged_amount_fails_default_quality_policy(pit_fixture):
    flagged = copy.deepcopy(pit_fixture.base)
    in_scope = flagged.daily_etf.index[
        flagged.daily_etf["date"].ge(pd.Timestamp(pit_fixture.boundaries.train_start))
    ][0]
    flagged.daily_etf.loc[in_scope, "missing_traded_value_count"] = 1
    flagged.daily_etf.loc[in_scope, "has_data_quality_flag"] = True

    with pytest.raises(PITContractError, match="quality"):
        pit_fixture.adapter.prepare(flagged, pit_fixture.boundaries, AFMLConfig())


def test_pit_authorized_halt_zero_amount_is_valid(pit_fixture):
    halted = copy.deepcopy(pit_fixture.base)
    in_scope = halted.daily_etf.index[
        halted.daily_etf["date"].ge(pd.Timestamp(pit_fixture.boundaries.train_start))
    ][0]
    halted.daily_etf.loc[in_scope, "has_data_quality_flag"] = True
    halted.daily_etf.loc[in_scope, "status_zero_authorized_count"] = 1

    inputs = pit_fixture.adapter.prepare(halted, pit_fixture.boundaries, AFMLConfig())

    validated = inputs.daily_etf.loc[
        inputs.daily_etf["date"].eq(halted.daily_etf.loc[in_scope, "date"])
        & inputs.daily_etf["etf_id"].eq(halted.daily_etf.loc[in_scope, "etf_id"])
    ]
    assert len(validated) == 1
    assert bool(validated.iloc[0]["has_data_quality_flag"])


def test_execution_mapping_fails_when_no_future_twse_session(pit_fixture):
    last = pit_fixture.inputs.daily_etf.iloc[-1]

    with pytest.raises(PITContractError, match="no TWSE execution session"):
        next_execution_session(
            pit_fixture.inputs.trading_calendar,
            last["source_available_at"],
            decision_cutoff="after_close",
        )


def test_verified_revision_manifests_propagate_to_source_rows(pit_fixture):
    base = copy.deepcopy(pit_fixture.base)
    for artifact_id in ("daily_price_volume", "trading_calendar"):
        path = pit_fixture.adapter.gateway.manifest_dir / f"{artifact_id}.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["availability_field"] = "source_available_date"
        manifest["revision_policy"] = "append_only_vintages"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
        base.metadata["manifest_hashes"][artifact_id] = hashlib.sha256(payload).hexdigest()

    inputs = pit_fixture.adapter.prepare(base, pit_fixture.boundaries, AFMLConfig())

    assert inputs.source_identity["source_revision_status"] == "PIT_REVISION_VERIFIED"
    assert inputs.daily_etf["source_revision_status"].eq("PIT_REVISION_VERIFIED").all()
    assert inputs.ix0001["source_revision_status"].eq("PIT_REVISION_VERIFIED").all()


def test_undeclared_calendar_manifest_coverage_is_explicit_limitation(pit_fixture):
    base = copy.deepcopy(pit_fixture.base)
    path = pit_fixture.adapter.gateway.manifest_dir / "trading_calendar.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["date_range"] = None
    manifest["availability_date_range"] = None
    path.write_text(json.dumps(manifest), encoding="utf-8")
    payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    base.metadata["manifest_hashes"]["trading_calendar"] = hashlib.sha256(
        payload
    ).hexdigest()

    inputs = pit_fixture.adapter.prepare(base, pit_fixture.boundaries, AFMLConfig())

    assert (
        inputs.source_identity["coverage"][
            "trading_calendar_manifest_coverage_declared"
        ]
        is False
    )
