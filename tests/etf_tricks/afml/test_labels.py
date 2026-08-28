from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from etf_tricks.afml import LabelConfig, TripleBarrierLabeler


@dataclass
class LabelFixture:
    config: LabelConfig
    features: pd.DataFrame
    bars: pd.DataFrame
    memberships: pd.DataFrame
    split_cutoffs: dict[str, dict[str, object]]
    train_end: pd.Timestamp
    train_cutoff: pd.Timestamp

    @property
    def inputs(self):
        return self.features, self.bars, self.memberships, self.split_cutoffs

    @property
    def tail_inputs(self):
        return (
            self.features[self.features["bar_id"].eq(6)],
            self.bars,
            self.memberships,
            self.split_cutoffs,
        )

    @property
    def delayed_inputs(self):
        delayed = self.memberships.assign(
            member_available_at=lambda x: x["member_available_at"]
            + pd.Timedelta(days=20)
        )
        return (
            self.features[self.features["bar_id"].eq(1)],
            self.bars,
            delayed,
            self.split_cutoffs,
        )


@pytest.fixture
def label_fixture() -> LabelFixture:
    dates = pd.bdate_range("2024-01-02", periods=14)
    available = dates.tz_localize("Asia/Taipei") + pd.Timedelta(hours=18)
    # Two daily closes per completed Dollar bar. Bar 2's first close jumps
    # enough to hit bar 1's upper barrier before bar 4's vertical close.
    nav = np.array(
        [100.0, 100.5, 101.0, 101.5, 104.0, 104.2, 103.5, 103.8,
         103.9, 104.0, 104.1, 104.2, 104.2, 104.2]
    )
    member_bar_id = np.repeat(np.arange(7), 2)
    memberships = pd.DataFrame(
        {
            "etf_id": "momentum",
            "bar_id": member_bar_id,
            "date": dates,
            "nav": nav,
            "member_available_at": available,
        }
    )
    bar_close = nav[1::2]
    bar_returns = np.r_[0.006, np.diff(np.log(bar_close))]
    bars = pd.DataFrame(
        {
            "etf_id": "momentum",
            "bar_id": np.arange(7),
            "bar_status": "FINALIZED",
            "bar_end_date": dates[1::2],
            "close_nav": bar_close,
            "log_return": bar_returns,
            "bar_available_at": available[1::2],
            "feature_available_at": available[1::2],
            "calibration_version": "q-v1",
            "config_version": "bar-v1",
        }
    )
    features = bars[
        ["etf_id", "bar_id", "bar_end_date", "log_return", "feature_available_at"]
    ].copy()
    config = LabelConfig(
        volatility_span=3,
        min_obs=2,
        pt_mult=1.0,
        sl_mult=1.0,
        vertical_bars=3,
    )
    train_end = pd.Timestamp(dates[-1])
    train_cutoff = available[-1]
    cutoffs = {
        "train": {
            "observation_start": pd.Timestamp(dates[0]),
            "observation_end": train_end,
            "decision_cutoff": train_cutoff,
        }
    }
    return LabelFixture(
        config, features, bars, memberships, cutoffs, train_end, train_cutoff
    )


def test_daily_close_first_touch_precedes_future_bar_close(label_fixture):
    tables = TripleBarrierLabeler(label_fixture.config).build(*label_fixture.inputs)
    event = tables.labels.query("event_id == 'momentum-1'").iloc[0]

    assert event["first_touch_type"] == "upper"
    assert event["first_touch_date"] < event["vertical_date"]
    assert event["label"] == 1
    assert event["label_available_at"] >= event["first_touch_source_available_at"]
    assert event["source_path_kind"] == "daily_close"


def test_unresolved_tail_is_not_shortened(label_fixture):
    event = TripleBarrierLabeler(label_fixture.config).build(
        *label_fixture.tail_inputs
    ).labels.iloc[0]

    assert event["label_status"] == "unresolved_tail"
    assert pd.isna(event["label"])
    assert pd.isna(event["t1"])


def test_training_label_requires_t1_and_availability_before_cutoff(label_fixture):
    labels = TripleBarrierLabeler(label_fixture.config).build(
        *label_fixture.delayed_inputs
    ).labels
    row = labels.iloc[0]

    assert row["t1"] <= label_fixture.train_end
    assert row["label_available_at"] > label_fixture.train_cutoff
    assert not bool(row["eligible_for_train"])


def test_vertical_zero_return_respects_drop_policy(label_fixture):
    features = label_fixture.features[label_fixture.features["bar_id"].eq(3)]
    bars = label_fixture.bars.copy()
    entry = bars.loc[bars["bar_id"].eq(3), "close_nav"].iloc[0]
    bars.loc[bars["bar_id"].eq(6), "close_nav"] = entry
    memberships = label_fixture.memberships.copy()
    memberships.loc[memberships["bar_id"].between(4, 6), "nav"] = entry

    row = TripleBarrierLabeler(label_fixture.config).build(
        features, bars, memberships, label_fixture.split_cutoffs
    ).labels.iloc[0]

    assert row["first_touch_type"] == "vertical"
    assert row["label_status"] == "zero_vertical_return"
    assert pd.isna(row["label"])


def test_events_store_overlap_evidence_without_mutating_features(label_fixture):
    before = label_fixture.features.copy(deep=True)
    tables = TripleBarrierLabeler(label_fixture.config).build(*label_fixture.inputs)

    assert {
        "event_concurrency_at_t0",
        "max_event_concurrency",
        "average_uniqueness",
    }.issubset(tables.events.columns)
    assert tables.events["average_uniqueness"].dropna().between(0, 1).all()
    assert "label" not in tables.events.columns
    pd.testing.assert_frame_equal(before, label_fixture.features)


def test_lower_touch_and_duplicate_membership_contract(label_fixture):
    features = label_fixture.features[label_fixture.features["bar_id"].eq(1)]
    memberships = label_fixture.memberships.copy()
    memberships.loc[memberships["bar_id"].eq(2), "nav"] = 95.0
    row = TripleBarrierLabeler(label_fixture.config).build(
        features,
        label_fixture.bars,
        memberships,
        label_fixture.split_cutoffs,
    ).labels.iloc[0]
    assert row["first_touch_type"] == "lower"
    assert row["label"] == -1

    duplicate = pd.concat([memberships, memberships.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        TripleBarrierLabeler(label_fixture.config).build(
            features,
            label_fixture.bars,
            duplicate,
            label_fixture.split_cutoffs,
        )
