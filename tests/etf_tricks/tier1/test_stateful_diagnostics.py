import hashlib
import json

import pandas as pd
import pytest

from etf_tricks.tier1.stateful_diagnostics import load_barrier_diagnostic, summarize_stateful_ledger


def test_stateful_summary_keeps_mark_to_market_separate_from_completed_trades() -> None:
    daily = pd.DataFrame(
        {
            "etf_id": "x",
            "date": pd.date_range("2024-01-02", periods=4, freq="B"),
            "strategy_nav": [100.0, 101.0, 102.0, 103.0],
            "mark_price_kind": "ETF_TRICK_DAILY_NAV_PROXY",
        }
    )
    trades = pd.DataFrame({"side": ["buy"], "commission": [1.0]})

    result = summarize_stateful_ledger(daily, trades)

    assert result.loc[0, "completed_round_trip_count"] == 0
    assert result.loc[0, "open_position_at_end"] == True
    assert result.loc[0, "performance_status"] == "MARK_TO_MARKET_ONLY"
    assert result.loc[0, "final_strategy_nav"] == pytest.approx(103.0)


def test_stateful_summary_rejects_multiple_etfs_or_non_proxy_mixing() -> None:
    daily = pd.DataFrame({"etf_id": ["x", "y"], "date": pd.to_datetime(["2024-01-02", "2024-01-03"]), "strategy_nav": [100.0, 101.0], "mark_price_kind": "ETF_TRICK_DAILY_NAV_PROXY"})
    with pytest.raises(ValueError, match="one ETF"):
        summarize_stateful_ledger(daily, pd.DataFrame(columns=["side", "commission"]))


def test_load_barrier_diagnostic_accepts_only_matching_v2_lineage(tmp_path) -> None:
    parent_manifest = tmp_path / "stateful-manifest.json"
    parent_manifest.write_text('{"ledger":"v1"}', encoding="utf-8")
    expected_parent_sha = hashlib.sha256(parent_manifest.read_bytes()).hexdigest()
    root = tmp_path / "barrier-v2"
    root.mkdir()
    table = root / "barrier_diagnostics.parquet"
    pd.DataFrame(
        {
            "scope": ["ALL_EVENTS", "CANDIDATES"],
            "event_count": [10, 4],
            "upper_touch_count": [3, 2],
            "lower_touch_count": [7, 2],
            "vertical_touch_count": [0, 0],
            "mean_time_to_touch_bars": [5.0, 4.0],
            "mean_mfe_log_return": [0.1, 0.2],
            "mean_mae_log_return": [-0.1, -0.2],
            "mean_post_upper_continuation_log_return": [0.03, 0.01],
        }
    ).to_parquet(table, index=False)
    root.joinpath("manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "tier1-stateful-barrier-diagnostics-v2",
                "etf_id": "x",
                "parent_stateful_manifest_sha256": expected_parent_sha,
                "tables": {"barrier_diagnostics": {"path": table.name}},
            }
        ),
        encoding="utf-8",
    )

    result = load_barrier_diagnostic(root, etf_id="x", parent_stateful_manifest_sha256=expected_parent_sha)

    assert set(result["scope"]) == {"ALL_EVENTS", "CANDIDATES"}
    with pytest.raises(ValueError, match="parent stateful"):
        load_barrier_diagnostic(root, etf_id="x", parent_stateful_manifest_sha256="0" * 64)
