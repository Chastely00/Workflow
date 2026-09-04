import pandas as pd

from etf_tricks.tier1 import splits


def test_purging_removes_overlapping_train_events_and_embargo() -> None:
    events = pd.DataFrame({"t0": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]), "t1": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])})

    train = splits.purged_train_indices(events, validation_indices=[2], embargo_rows=1)

    assert train.tolist() == [0]


def test_walk_forward_folds_train_only_on_fully_resolved_past_events() -> None:
    events = pd.DataFrame(
        {
            "t0": pd.date_range("2024-01-01", periods=6),
            "t1": pd.date_range("2024-01-02", periods=6),
        }
    )

    folds = splits.chronological_purged_folds(events, n_splits=2)

    assert [(train.tolist(), valid.tolist()) for train, valid in folds] == [([0], [2, 3]), ([0, 1, 2], [4, 5])]
    for train, valid in folds:
        assert (events.iloc[train]["t1"] < events.iloc[valid]["t0"].min()).all()


def test_fold_audit_records_persist_actual_event_boundaries_and_purge_proof() -> None:
    events = pd.DataFrame(
        {
            "t0": pd.date_range("2024-01-01", periods=6),
            "t1": pd.date_range("2024-01-02", periods=6),
        }
    )
    folds = splits.chronological_purged_folds(events, n_splits=2)

    audit = splits.fold_audit_records(events, folds)

    assert audit.to_dict("records") == [
        {
            "outer_fold": 0,
            "train_rows": 1,
            "validation_rows": 2,
            "train_t0_min": pd.Timestamp("2024-01-01"),
            "train_t0_max": pd.Timestamp("2024-01-01"),
            "train_t1_max": pd.Timestamp("2024-01-02"),
            "validation_t0_min": pd.Timestamp("2024-01-03"),
            "validation_t0_max": pd.Timestamp("2024-01-04"),
            "validation_t1_max": pd.Timestamp("2024-01-05"),
            "event_end_purge_verified": True,
            "embargo_policy": "NOT_APPLICABLE_FORWARD_ONLY",
        },
        {
            "outer_fold": 1,
            "train_rows": 3,
            "validation_rows": 2,
            "train_t0_min": pd.Timestamp("2024-01-01"),
            "train_t0_max": pd.Timestamp("2024-01-03"),
            "train_t1_max": pd.Timestamp("2024-01-04"),
            "validation_t0_min": pd.Timestamp("2024-01-05"),
            "validation_t0_max": pd.Timestamp("2024-01-06"),
            "validation_t1_max": pd.Timestamp("2024-01-07"),
            "event_end_purge_verified": True,
            "embargo_policy": "NOT_APPLICABLE_FORWARD_ONLY",
        },
    ]


def test_average_uniqueness_weights_overlapping_event_intervals() -> None:
    events = pd.DataFrame(
        {
            "t0": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "t1": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-03"]),
        }
    )

    result = splits.average_uniqueness(events)

    # Concurrency is [1, 2, 2].  The event averages are [0.75, 0.5, 0.5].
    assert result.tolist() == [0.75, 0.5, 0.5]


def test_average_uniqueness_excludes_non_trading_days_from_event_concurrency() -> None:
    events = pd.DataFrame(
        {
            "t0": pd.to_datetime(["2024-01-05", "2024-01-08"]),
            "t1": pd.to_datetime(["2024-01-09", "2024-01-09"]),
        }
    )

    result = splits.average_uniqueness(
        events,
        trading_sessions=pd.to_datetime(["2024-01-05", "2024-01-08", "2024-01-09"]),
    )

    # Sessions have concurrency [1, 2, 2]; Saturday and Sunday are not observations.
    assert result.tolist() == [2 / 3, 0.5]


def test_average_uniqueness_does_not_treat_distinct_etfs_as_duplicate_events() -> None:
    events = pd.DataFrame(
        {
            "etf_id": ["a", "b"],
            "t0": pd.to_datetime(["2024-01-02", "2024-01-02"]),
            "t1": pd.to_datetime(["2024-01-03", "2024-01-03"]),
        }
    )

    result = splits.average_uniqueness(
        events,
        trading_sessions=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        entity_column="etf_id",
    )

    assert result.tolist() == [1.0, 1.0]
