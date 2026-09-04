import pandas as pd

from etf_tricks.tier2.diagnostics import summarize_tier2_oof


def test_tier2_diagnostics_compares_accepted_net_return_to_all_candidates() -> None:
    frame = pd.DataFrame({"event_id": ["a", "b", "c"], "y_meta": [1, 0, 1]})
    predictions = pd.DataFrame({"p2": [0.9, 0.2, 0.8], "accepted": [True, False, True]})
    targets = pd.DataFrame({"event_id": ["a", "b", "c"], "net_log_return": [0.03, -0.02, 0.01]})

    metrics = summarize_tier2_oof(frame, predictions, targets)

    assert metrics["candidate_mean_net_log_return"] == 0.006666666666666665
    assert metrics["accepted_mean_net_log_return"] == 0.02
    assert metrics["accepted_positive_rate"] == 1.0
