import pandas as pd
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from etf_tricks.tier1.market_snapshot import ExecutionMarketSnapshot


def test_snapshot_uses_previous_holdings_and_raw_open() -> None:
    holdings = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-02")],
            "etf_id": ["x", "x"],
            "ticker": ["A", "B"],
            "actual_weight": [0.6, 0.4],
        }
    )
    prices = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-03")],
            "ticker": ["A", "B"],
            "open": [110.0, 105.0],
            "previous_close": [100.0, 100.0],
            "source_available_at": pd.to_datetime(["2024-01-03 13:30+08:00"] * 2),
            "is_legal_execution": [True, True],
        }
    )

    nav = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "etf_id": ["x"], "nav": [1000.0]})
    result = ExecutionMarketSnapshot.from_frames(holdings, prices, nav)

    row = result.iloc[0]
    assert row["date"] == pd.Timestamp("2024-01-03")
    assert row["raw_open_nav"] == 1080.0
    assert row["is_legal_execution"]


def test_snapshot_fails_closed_when_a_constituent_is_not_executable() -> None:
    holdings = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "etf_id": ["x"], "ticker": ["A"], "actual_weight": [1.0]})
    prices = pd.DataFrame({"date": [pd.Timestamp("2024-01-03")], "ticker": ["A"], "open": [110.0], "previous_close": [100.0], "source_available_at": pd.to_datetime(["2024-01-03 13:30+08:00"]), "is_legal_execution": [False]})

    nav = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "etf_id": ["x"], "nav": [1000.0]})
    result = ExecutionMarketSnapshot.from_frames(holdings, prices, nav)

    assert not result.iloc[0]["is_legal_execution"]
    assert result.iloc[0]["raw_open_nav"] != result.iloc[0]["raw_open_nav"]


def test_snapshot_carries_cash_when_constituent_weights_sum_below_one() -> None:
    holdings = pd.DataFrame({
        "date": [pd.Timestamp("2024-01-02")], "etf_id": ["x"], "ticker": ["A"], "actual_weight": [0.6],
    })
    prices = pd.DataFrame({
        "date": [pd.Timestamp("2024-01-03")], "ticker": ["A"], "open": [110.0], "previous_close": [100.0],
        "source_available_at": pd.to_datetime(["2024-01-03 13:30+08:00"]), "is_legal_execution": [True],
    })
    nav = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "etf_id": ["x"], "nav": [1000.0]})

    result = ExecutionMarketSnapshot.from_frames(holdings, prices, nav)

    row = result.iloc[0]
    assert row["is_legal_execution"]
    assert row["raw_open_nav"] == 1060.0


def test_prepare_prices_derives_previous_close_and_requires_trading_state() -> None:
    prices = pd.DataFrame({"date": pd.to_datetime(["2024-01-02", "2024-01-03"]), "ticker": ["A", "A"], "open": [100.0, 110.0], "close": [101.0, 111.0]})
    states = pd.DataFrame({"date": pd.to_datetime(["2024-01-03"]), "ticker": ["A"], "market_state": ["TRADING"], "exchange_tradable": [True], "source_available_date": pd.to_datetime(["2024-01-03"])})

    result = ExecutionMarketSnapshot.prepare_prices(prices, states)

    row = result.iloc[0]
    assert row["previous_close"] == 101.0
    assert row["is_legal_execution"]


def test_prepare_prices_uses_later_availability_when_price_and_state_both_report_it() -> None:
    prices = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]), "ticker": ["A", "A"],
        "open": [100.0, 110.0], "close": [101.0, 111.0],
        "source_available_date": pd.to_datetime(["2024-01-02 12:00+08:00", "2024-01-03 12:00+08:00"]),
    })
    states = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-03"]), "ticker": ["A"], "market_state": ["TRADING"],
        "exchange_tradable": [True], "source_available_date": pd.to_datetime(["2024-01-03 13:30+08:00"]),
    })

    result = ExecutionMarketSnapshot.prepare_prices(prices, states)

    assert result.iloc[0]["source_available_at"] == pd.Timestamp("2024-01-03 13:30+08:00")


def test_loader_rejects_wrong_manifest_identity(tmp_path) -> None:
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests" / "daily_price_volume.json").write_text(json.dumps({"artifact_id": "wrong", "artifact_paths": []}), encoding="utf-8")
    (tmp_path / "manifests" / "daily_market_state.json").write_text(json.dumps({"artifact_id": "daily_market_state", "artifact_paths": []}), encoding="utf-8")

    try:
        ExecutionMarketSnapshot.read_canonical(tmp_path, [2024])
    except ValueError as exc:
        assert "manifest" in str(exc)
    else:
        raise AssertionError("expected manifest identity rejection")


def test_bounded_loader_requires_manifest_declared_constituent_price_snapshot(tmp_path) -> None:
    root = Path(tmp_path)
    (root / "manifests").mkdir()
    price_path = "canonical/raw/daily_price_volume_etf_constituents/versions/v1/year=2024/part.parquet"
    state_path = "canonical/derived/daily_market_state/versions/v1/year=2024/part.parquet"
    for relative, rows in (
        (price_path, [{"date": "2024-01-02", "ticker": "A", "open": 100.0, "close": 101.0}]),
        (state_path, [{"date": "2024-01-02", "ticker": "A", "market_state": "TRADING"}]),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), path)
    (root / "manifests" / "daily_price_volume_etf_constituents.json").write_text(
        json.dumps({
            "artifact_id": "daily_price_volume_etf_constituents", "status": "ready",
            "source_price_semantics": "raw_unadjusted_execution_prices",
            "data_cutoff_at": "2026-09-04T02:00:00Z", "artifact_paths": [price_path],
        }), encoding="utf-8",
    )
    (root / "manifests" / "daily_market_state.json").write_text(
        json.dumps({"artifact_id": "daily_market_state", "status": "ready", "artifact_paths": [state_path]}),
        encoding="utf-8",
    )

    prices, states = ExecutionMarketSnapshot.read_bounded_constituent_snapshot(root, [2024])

    assert prices[["date", "ticker", "open", "close"]].to_dict("records") == [
        {"date": "2024-01-02", "ticker": "A", "open": 100.0, "close": 101.0}
    ]
    assert states["market_state"].tolist() == ["TRADING"]
