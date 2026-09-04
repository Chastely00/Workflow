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
