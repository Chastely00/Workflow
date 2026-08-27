from __future__ import annotations

import json
import copy
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etf_tricks import ETF_IDS, ETFTrickLab
from etf_tricks.features import PITFeatureEngine
from etf_tricks.execution import PortfolioExecutionEngine


def _publish(root: Path, artifact_id: str, frame: pd.DataFrame) -> None:
    store = root / "data_store"
    data_dir = store / "canonical"
    manifest_dir = store / "manifests"
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{artifact_id}.parquet"
    frame.to_parquet(path, index=False)
    date_column = "date" if "date" in frame else "source_available_date" if "source_available_date" in frame else None
    manifest = {
        "artifact_id": artifact_id,
        "status": "ready",
        "columns": frame.columns.tolist(),
        "artifact_paths": [str(path.relative_to(store)).replace("\\", "/")],
        "row_count": len(frame),
        "duplicate_count": 0,
        "logical_key": {
            "trading_calendar": ["date", "market"],
            "daily_price_volume": ["date", "ticker"],
            "daily_chip": ["date", "ticker"],
            "monthly_sales": ["source_row_id"],
            "financial_statement_raw": ["source_row_id"],
            "security_master": ["ticker"],
        }[artifact_id],
    }
    if date_column is not None:
        dates = pd.to_datetime(frame[date_column])
        manifest["date_range"] = [str(dates.min().date()), str(dates.max().date())]
    (manifest_dir / f"{artifact_id}.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _data_analysts_fixture(root: Path) -> tuple[pd.Timestamp, pd.Timestamp]:
    dates = pd.bdate_range("2023-12-01", "2025-02-07")
    run_start, run_end = pd.Timestamp("2025-02-03"), pd.Timestamp("2025-02-07")
    tickers = [str(1101 + index) for index in range(10)]
    calendar = pd.DataFrame(
        {"date": dates, "market": "TWSE", "is_trading_day": True}
    )
    _publish(root, "trading_calendar", calendar)

    daily_rows = []
    chip_rows = []
    for day, date in enumerate(dates):
        daily_rows.append(
            {
                "date": date,
                "ticker": "IX0001",
                "close": 20_000.0,
                "adj_close": 20_000.0,
                "volume": 1_000_000.0,
                "traded_value": 1_000_000_000.0,
                "turnover": 0.0,
                "market_cap": 0.0,
            }
        )
        for index, ticker in enumerate(tickers):
            close = 50.0 + index * 2 + day * 0.03 + np.sin(day / (5 + index)) * 2
            daily_rows.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "close": close,
                    "adj_close": close,
                    "volume": 100_000.0 + day * 100 + index * 1_000,
                    "traded_value": 3_000_000.0 + index * 100_000,
                    "turnover": 0.01 + index * 0.001,
                    "market_cap": 10_000_000_000.0 + index * 1_000_000_000,
                }
            )
            chip_rows.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "qfii_examt": 100.0 + index,
                    "fund_examt": 50.0 + index,
                    "dlrp_examt": -10.0 + index,
                }
            )
    _publish(root, "daily_price_volume", pd.DataFrame(daily_rows))
    _publish(root, "daily_chip", pd.DataFrame(chip_rows))

    sales = pd.DataFrame(
        {
            "ticker": tickers,
            "r18": np.arange(10, 20, dtype=float),
            "source_period_date": pd.Timestamp("2025-01-01"),
            "source_available_date": pd.Timestamp("2025-01-15"),
            "source_row_id": [f"sales-{ticker}" for ticker in tickers],
        }
    )
    _publish(root, "monthly_sales", sales)
    financial = pd.DataFrame(
        {
            "ticker": tickers,
            "r103": np.arange(10, 20, dtype=float),
            "no": "TTM",
            "merg": "Y",
            "curr": "NTD",
            "period_end_date": pd.Timestamp("2024-12-31"),
            "source_available_date": pd.Timestamp("2025-01-20"),
            "revision_date": pd.Timestamp("2025-01-20"),
            "source_row_id": [f"roe-{ticker}" for ticker in tickers],
        }
    )
    _publish(root, "financial_statement_raw", financial)
    industries = [
        "M2800 Financial Industry",
        "OTC28 OTC Banking",
        "M2600 Shipping and Transportation",
        "OTC26 OTC Transporation",
    ] + ["M1100 Cement Industry"] * 6
    master = pd.DataFrame(
        {
            "ticker": tickers,
            "stock_name": [f"Stock {ticker}" for ticker in tickers],
            "list_date": pd.Timestamp("2020-01-01"),
            "delist_date": pd.NaT,
            "main_industry": industries,
        }
    )
    _publish(root, "security_master", master)
    return run_start, run_end


def test_manifest_backed_public_api_produces_13_continuous_curves(tmp_path):
    start, end = _data_analysts_fixture(tmp_path)
    lab = ETFTrickLab.from_data_analysts(tmp_path)
    result = lab.run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    assert result.nav.columns.tolist() == list(ETF_IDS)
    assert result.amount.columns.tolist() == list(ETF_IDS)
    assert result.daily_etf.groupby("etf_id")["date"].nunique().eq(5).all()
    assert not result.daily_etf.duplicated(["date", "etf_id"]).any()
    reconciliation = result.daily_etf.merge(
        result.daily_holdings.groupby(["date", "etf_id"], as_index=False)["market_value"].sum(),
        on=["date", "etf_id"],
        how="left",
    )
    assert np.allclose(
        reconciliation["total_assets"],
        reconciliation["cash"] + reconciliation["market_value"],
    )

    readiness = lab.validate(result)
    assert readiness.status == "READY", readiness.hard_failures

    allocation = lab.allocate(
        etf_id="momentum",
        as_of_date="2025-01-31",
        capital=Decimal("2345678"),
    )
    assert allocation.status == "ready"
    assert allocation.supplied_capital == Decimal("2345678")
    assert allocation.basket["target_shares"].sum() > 0

    rebalance = lab.rebalance(
        etf_id="momentum",
        as_of_date="2025-01-31",
        current_positions={"IX0001": 1},
        current_cash=Decimal("2345678"),
        capital_delta=Decimal("0"),
    )
    assert rebalance.orders.set_index("ticker").loc["IX0001", "side"] == "sell"

    with pytest.raises(ValueError, match="formation_date"):
        lab.allocate(
            etf_id="momentum",
            as_of_date="2025-02-03",
            capital=Decimal("2345678"),
        )

    stale = copy.deepcopy(result)
    stale.metadata["manifest_hashes"]["daily_price_volume"] = "stale"
    stale.metadata["spec_hash"] = "stale"
    stale_report = lab.validate(stale)
    assert stale_report.status == "NOT_READY"
    assert {issue.code for issue in stale_report.hard_failures}.issuperset(
        {"source_identity_mismatch", "spec_identity_mismatch"}
    )

    sales_manifest_path = tmp_path / "data_store" / "manifests" / "monthly_sales.json"
    sales_manifest = json.loads(sales_manifest_path.read_text(encoding="utf-8"))
    sales_manifest["date_range"] = ["2024-01-01", "2024-01-01"]
    sales_manifest_path.write_text(json.dumps(sales_manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="monthly_sales.*stale"):
        ETFTrickLab.from_data_analysts(tmp_path).run_all(
            start_date=start,
            end_date=end,
            initial_capital=Decimal("1234567"),
        )


def test_run_all_computes_all_formation_features_in_one_batch(tmp_path, monkeypatch):
    start, end = _data_analysts_fixture(tmp_path)
    calls: list[tuple[pd.Timestamp, ...]] = []
    original = PITFeatureEngine.compute_many

    def recording_compute_many(self, formation_dates):
        normalized = tuple(pd.Timestamp(value) for value in formation_dates)
        calls.append(normalized)
        return original(self, normalized)

    monkeypatch.setattr(PITFeatureEngine, "compute_many", recording_compute_many)

    ETFTrickLab.from_data_analysts(tmp_path).run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    assert len(calls) == 1
    assert calls[0] == (pd.Timestamp("2025-01-31"),)


def test_run_all_prepares_the_execution_market_once_for_all_etfs(tmp_path, monkeypatch):
    start, end = _data_analysts_fixture(tmp_path)
    calls: list[int] = []
    original = PortfolioExecutionEngine.prepare_market

    def recording_prepare_market(market):
        calls.append(len(market))
        return original(market)

    monkeypatch.setattr(
        PortfolioExecutionEngine,
        "prepare_market",
        staticmethod(recording_prepare_market),
    )

    ETFTrickLab.from_data_analysts(tmp_path).run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    assert len(calls) == 1


def test_run_all_rejects_physically_truncated_trading_calendar(tmp_path):
    start, end = _data_analysts_fixture(tmp_path)
    calendar_path = tmp_path / "data_store" / "canonical" / "trading_calendar.parquet"
    calendar = pd.read_parquet(calendar_path)
    calendar = calendar[pd.to_datetime(calendar["date"]).lt(end)]
    calendar.to_parquet(calendar_path, index=False)
    manifest_path = tmp_path / "data_store" / "manifests" / "trading_calendar.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["row_count"] = len(calendar)
    manifest["date_range"] = [
        str(pd.to_datetime(calendar["date"]).min().date()),
        str(pd.to_datetime(calendar["date"]).max().date()),
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="trading_calendar physical coverage"):
        ETFTrickLab.from_data_analysts(tmp_path).run_all(
            start_date=start,
            end_date=end,
            initial_capital=Decimal("1234567"),
        )
