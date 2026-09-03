import pandas as pd

from etf_tricks.tier1.splits import purged_train_indices


def test_purging_removes_overlapping_train_events_and_embargo() -> None:
    events = pd.DataFrame({"t0": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]), "t1": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])})

    train = purged_train_indices(events, validation_indices=[2], embargo_rows=1)

    assert train.tolist() == [0]
