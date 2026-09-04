import pandas as pd
import pytest

from etf_tricks.tier1.targets import Tier1TargetConfig
from etf_tricks.tier1.target_materializer import build_target_metadata, build_target_table


def test_build_target_table_uses_prior_holdings_raw_open_and_membership_availability() -> None:
    tz = "+08:00"
    dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
    bars = pd.DataFrame(
        {
            "etf_id": ["x"] * 4,
            "bar_id": [1, 2, 3, 4],
            "bar_end_date": dates[:4],
            "close_nav": [100.0, 110.0, 120.0, 130.0],
            "feature_available_at": pd.to_datetime(
                [f"2024-01-0{i} 13:30{tz}" for i in range(1, 5)]
            ),
        }
    )
    holdings = pd.DataFrame(
        {"date": [dates[0]], "etf_id": ["x"], "ticker": ["AAA"], "actual_weight": [1.0]}
    )
    prices = pd.DataFrame(
        {
            "date": dates[1:],
            "ticker": ["AAA"] * 4,
            "open": [100.0, 100.0, 110.0, 120.0],
            "close": [100.0, 100.0, 110.0, 120.0],
            "source_available_date": pd.to_datetime(
                [f"2024-01-0{i} 13:30{tz}" for i in range(2, 6)]
            ),
        }
    )
    states = pd.DataFrame(
        {
            "date": dates[1:],
            "ticker": ["AAA"] * 4,
            "market_state": ["TRADING"] * 4,
            "exchange_tradable": [True] * 4,
            "source_available_date": pd.to_datetime(
                [f"2024-01-0{i} 13:30{tz}" for i in range(2, 6)]
            ),
        }
    )
    daily_nav = pd.DataFrame({"date": dates[:4], "etf_id": ["x"] * 4, "nav": [100.0, 100.0, 100.0, 130.0]})
    membership = pd.DataFrame(
        {
            "date": dates[:4],
            "etf_id": ["x"] * 4,
            "member_available_at": pd.to_datetime(
                [f"2024-01-0{i} 13:30{tz}" for i in range(1, 5)]
            ),
        }
    )

    targets = build_target_table(
        bars,
        holdings,
        prices,
        states,
        daily_nav,
        membership,
        config=Tier1TargetConfig(volatility_span=2, min_obs=2, vertical_bars=1),
    )

    event = targets.loc[targets["event_id"].eq("x-3")].iloc[0]
    assert event["entry_raw_open"] == pytest.approx(110.0)
    assert event["exit_raw_open"] == pytest.approx(100.0 * 120.0 / 110.0)
    assert event["label_available_at"] == pd.Timestamp("2024-01-05 13:30+08:00")


def test_target_metadata_binds_each_immutable_input_manifest(tmp_path) -> None:
    manifests = {}
    for name in ("afml", "etf", "price", "state"):
        path = tmp_path / f"{name}.json"
        path.write_text(name, encoding="utf-8")
        manifests[name] = path

    metadata = build_target_metadata(
        afml_manifest_path=manifests["afml"],
        etf_manifest_path=manifests["etf"],
        price_manifest_path=manifests["price"],
        market_state_manifest_path=manifests["state"],
        start_date="2020-01-01",
        end_date="2026-07-07",
        config=Tier1TargetConfig(),
    )

    assert set(metadata).issuperset(
        {
            "afml_manifest_sha256",
            "etf_manifest_sha256",
            "price_manifest_sha256",
            "market_state_manifest_sha256",
            "requested_date_range",
            "target_config",
        }
    )
    assert metadata["requested_date_range"] == ["2020-01-01", "2026-07-07"]
