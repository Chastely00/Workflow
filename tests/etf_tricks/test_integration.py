from __future__ import annotations

import json
import copy
import hashlib
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

import etf_tricks.lab as lab_module
from etf_tricks import ETF_IDS, ETFTrickLab
from etf_tricks.features import PITFeatureEngine
from etf_tricks.execution import PortfolioExecutionEngine
from etf_tricks.universe import UniverseEngine


_SHA256 = "a" * 64
_MARKET_STATE_COLUMNS = (
    "date", "ticker", "market", "market_state", "state_reason",
    "amount_state", "authoritative_traded_value", "amount_zero_authorized",
    "price_row_present", "attr_row_present", "atten_fg", "disp_fg",
    "full_fg", "limit_fg", "limo_fg", "sbadt_fg", "ssadt_fg",
    "susp_fg", "exchange_tradable", "full_delivery", "instrument_kind",
    "identity_source", "security_master_market", "lifecycle_list_date",
    "lifecycle_delist_date", "lifecycle_interval_start",
    "lifecycle_interval_end_exclusive", "lifecycle_active",
    "lifecycle_conflict", "identity_conflict", "lifecycle_pit_status",
    "revision_pit_status", "observation_date", "source_available_date",
    "availability_precision", "earliest_execution_session",
    "security_master_manifest_sha256", "calendar_manifest_sha256",
    "price_manifest_sha256", "tradability_manifest_sha256",
    "classification_policy_version", "data_cutoff_at",
)


def _publish_daily_market_state(
    root: Path,
    daily: pd.DataFrame,
    build_start: pd.Timestamp,
    build_end: pd.Timestamp,
) -> None:
    rows: list[dict[str, object]] = []
    selected = daily[
        pd.to_datetime(daily["date"]).between(build_start, build_end)
    ]
    interval_end = build_end + pd.Timedelta(days=1)
    for source in selected.itertuples(index=False):
        row_date = pd.Timestamp(source.date)
        is_index = str(source.ticker) == "IX0001"
        raw_flag = None if is_index else "N"
        rows.append(
            {
                "date": row_date,
                "ticker": str(source.ticker),
                "market": "INDEX" if is_index else "TWSE",
                "market_state": "TRADING",
                "state_reason": "APIPRCD_OBSERVED_AMOUNT",
                "amount_state": "OBSERVED",
                "authoritative_traded_value": float(source.traded_value),
                "amount_zero_authorized": False,
                "price_row_present": True,
                "attr_row_present": not is_index,
                "atten_fg": raw_flag,
                "disp_fg": raw_flag,
                "full_fg": raw_flag,
                "limit_fg": raw_flag,
                "limo_fg": raw_flag,
                "sbadt_fg": raw_flag,
                "ssadt_fg": raw_flag,
                "susp_fg": raw_flag,
                "exchange_tradable": True,
                "full_delivery": None if is_index else False,
                "instrument_kind": "INDEX" if is_index else "EQUITY",
                "identity_source": (
                    "APIPRCD_PRICE_ROW" if is_index else "SECURITY_MASTER_SNAPSHOT"
                ),
                "security_master_market": None if is_index else "TWSE",
                "lifecycle_list_date": None if is_index else "2020-01-01",
                "lifecycle_delist_date": None,
                "lifecycle_interval_start": (
                    None if is_index else str(build_start.date())
                ),
                "lifecycle_interval_end_exclusive": (
                    None if is_index else str(interval_end.date())
                ),
                "lifecycle_active": not is_index,
                "lifecycle_conflict": False,
                "identity_conflict": False,
                "lifecycle_pit_status": "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED",
                "revision_pit_status": "PIT_REVISION_UNVERIFIED",
                "observation_date": row_date,
                "source_available_date": row_date,
                "availability_precision": "AFTER_CLOSE_DATE_ONLY",
                "earliest_execution_session": row_date + pd.offsets.BDay(1),
                "security_master_manifest_sha256": _SHA256,
                "calendar_manifest_sha256": _SHA256,
                "price_manifest_sha256": _SHA256,
                "tradability_manifest_sha256": _SHA256,
                "classification_policy_version": "daily_market_state_v3",
                "data_cutoff_at": f"{row_date.date()}T14:00:00Z",
            }
        )

    frame = pd.DataFrame(rows, columns=_MARKET_STATE_COLUMNS)
    store = root / "data_store"
    relative = "canonical/derived/daily_market_state/year=2025/part.parquet"
    parquet_path = store / relative
    manifest_dir = store / "manifests"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(parquet_path, index=False)
    content_sha256 = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
    schema_fingerprint = hashlib.sha256(
        pq.read_schema(parquet_path).serialize().to_pybytes()
    ).hexdigest()
    manifest = {
        "artifact_id": "daily_market_state",
        "artifact_paths": [relative],
        "columns": list(_MARKET_STATE_COLUMNS),
        "date_range": [str(build_start.date()), str(build_end.date())],
        "availability_date_range": [str(build_start.date()), str(build_end.date())],
        "status": "ready",
        "row_count": len(frame),
        "duplicate_count": 0,
        "logical_key": ["date", "ticker"],
        "schema_version": "1.0",
        "schema_fingerprint": schema_fingerprint,
        "active_version": "market-state-v3",
        "source_families": [
            "security_master", "trading_calendar", "daily_price_volume",
            "daily_tradability",
        ],
        "dependency_versions": {
            "security_master": "security-master-v1",
            "trading_calendar": "calendar-v1",
            "daily_price_volume": "dpv-v1",
            "daily_tradability": "tradability-v1",
        },
        "dependency_manifest_sha256_by_contract": {
            "security_master": _SHA256,
            "trading_calendar": _SHA256,
            "daily_price_volume": _SHA256,
            "daily_tradability": _SHA256,
        },
        "dependency_certification_fingerprint": "certification-v1",
        "build_start": str(build_start.date()),
        "build_end": str(build_end.date()),
        "certified_source_start": str(build_start.date()),
        "classification_policy_version": "daily_market_state_v3",
        "state_lattice_policy_version": "daily_market_state_lattice_v5",
        "market_identity_policy_version": "daily_market_identity_v3",
        "partition_inventory": [
            {
                "partition_value": "2025",
                "path": relative,
                "size": parquet_path.stat().st_size,
                "row_count": len(frame),
                "schema_fingerprint": schema_fingerprint,
                "content_sha256": content_sha256,
            }
        ],
    }
    (manifest_dir / "daily_market_state.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


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
    daily = pd.DataFrame(daily_rows)
    _publish(root, "daily_price_volume", daily)
    _publish_daily_market_state(root, daily, run_start, run_end)
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


def test_run_all_prepares_one_universe_context_per_formation(tmp_path, monkeypatch):
    start, end = _data_analysts_fixture(tmp_path)
    calls: list[pd.Timestamp] = []
    original = UniverseEngine.prepare

    def recording_prepare(self, formation_date, features, security_master, ix0001):
        calls.append(pd.Timestamp(formation_date))
        return original(self, formation_date, features, security_master, ix0001)

    monkeypatch.setattr(UniverseEngine, "prepare", recording_prepare)

    ETFTrickLab.from_data_analysts(tmp_path).run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    assert calls == [pd.Timestamp("2025-01-31")]


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


def test_run_all_clamps_feature_warmup_to_each_artifact_coverage_start(
    tmp_path, monkeypatch
):
    start, end = _data_analysts_fixture(tmp_path)
    monkeypatch.setattr(lab_module, "ETF_IDS", ("market_cap",))
    calculated_warmup_start = pd.Timestamp("2024-02-14")
    for artifact_id in ("daily_price_volume", "daily_chip"):
        parquet_path = (
            tmp_path / "data_store" / "canonical" / f"{artifact_id}.parquet"
        )
        frame = pd.read_parquet(parquet_path)
        frame = frame[
            pd.to_datetime(frame["date"]).gt(calculated_warmup_start)
        ].copy()
        frame.to_parquet(parquet_path, index=False)
        manifest_path = (
            tmp_path / "data_store" / "manifests" / f"{artifact_id}.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["row_count"] = len(frame)
        manifest["date_range"] = [
            str(pd.to_datetime(frame["date"]).min().date()),
            str(pd.to_datetime(frame["date"]).max().date()),
        ]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = ETFTrickLab.from_data_analysts(tmp_path).run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    assert result.daily_etf["etf_id"].unique().tolist() == ["market_cap"]
