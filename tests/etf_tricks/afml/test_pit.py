from __future__ import annotations

import copy
import hashlib
import json

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


def test_current_manifest_hash_mismatch_fails_closed(pit_fixture):
    stale = copy.deepcopy(pit_fixture.base)
    stale.metadata["manifest_hashes"]["daily_price_volume"] = "stale"

    with pytest.raises(PITContractError, match="daily_price_volume"):
        pit_fixture.adapter.prepare(stale, pit_fixture.boundaries, AFMLConfig())


def test_flagged_amount_fails_default_quality_policy(pit_fixture):
    flagged = copy.deepcopy(pit_fixture.base)
    flagged.daily_etf.loc[0, "missing_traded_value_count"] = 1
    flagged.daily_etf.loc[0, "has_data_quality_flag"] = True

    with pytest.raises(PITContractError, match="quality"):
        pit_fixture.adapter.prepare(flagged, pit_fixture.boundaries, AFMLConfig())


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
