from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

import etf_tricks.lab as lab_module
from etf_tricks import ETF_IDS, ETFTrickLab, ETFTrickResult
from etf_tricks.features import PITFeatureEngine
from etf_tricks.data_gateway import DataContractError
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


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _publish_daily_market_state(
    root: Path,
    daily: pd.DataFrame,
    build_start: pd.Timestamp,
    build_end: pd.Timestamp,
    *,
    halted_without_price: set[tuple[pd.Timestamp, str]] | None = None,
) -> None:
    rows: list[dict[str, object]] = []
    selected = daily[
        pd.to_datetime(daily["date"]).between(build_start, build_end)
    ]
    interval_end = build_end + pd.Timedelta(days=1)
    halted_keys = halted_without_price or set()
    for source in selected.itertuples(index=False):
        row_date = pd.Timestamp(source.date)
        is_index = str(source.ticker) == "IX0001"
        key = (row_date, str(source.ticker))
        is_halted = key in halted_keys
        raw_flag = None if is_index else "N"
        rows.append(
            {
                "date": row_date,
                "ticker": str(source.ticker),
                "market": "INDEX" if is_index else "TWSE",
                "market_state": "HALTED" if is_halted else "TRADING",
                "state_reason": (
                    "APISTKATTR_SUSPENSION_NO_PRICE"
                    if is_halted
                    else "APIPRCD_OBSERVED_AMOUNT"
                ),
                "amount_state": "ZERO_AUTHORIZED" if is_halted else "OBSERVED",
                "authoritative_traded_value": (
                    0.0 if is_halted else float(source.traded_value)
                ),
                "amount_zero_authorized": is_halted,
                "price_row_present": not is_halted,
                "attr_row_present": not is_index,
                "atten_fg": raw_flag,
                "disp_fg": raw_flag,
                "full_fg": raw_flag,
                "limit_fg": raw_flag,
                "limo_fg": raw_flag,
                "sbadt_fg": raw_flag,
                "ssadt_fg": raw_flag,
                "susp_fg": "Y" if is_halted else raw_flag,
                "exchange_tradable": not is_halted,
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
        "dependency_certification_fingerprint": "b" * 64,
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


def _rewrite_market_state_rows(root: Path, frame: pd.DataFrame) -> None:
    store = root / "data_store"
    parquet_path = (
        store / "canonical/derived/daily_market_state/year=2025/part.parquet"
    )
    manifest_path = store / "manifests/daily_market_state.json"
    frame.loc[:, list(_MARKET_STATE_COLUMNS)].to_parquet(parquet_path, index=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema_fingerprint = hashlib.sha256(
        pq.read_schema(parquet_path).serialize().to_pybytes()
    ).hexdigest()
    inventory = manifest["partition_inventory"][0]
    manifest["row_count"] = len(frame)
    manifest["schema_fingerprint"] = schema_fingerprint
    inventory["size"] = parquet_path.stat().st_size
    inventory["row_count"] = len(frame)
    inventory["schema_fingerprint"] = schema_fingerprint
    inventory["content_sha256"] = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


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


def _data_analysts_fixture(
    root: Path,
    *,
    state_only_halt_key: tuple[str, str] | None = None,
    dpv_only_key: tuple[str, str] | None = None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
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
    complete_daily = pd.DataFrame(daily_rows)
    halted_keys: set[tuple[pd.Timestamp, str]] = set()
    published_daily = complete_daily.copy()
    if state_only_halt_key is not None:
        halt_date_value, halt_ticker = state_only_halt_key
        halted_date = pd.Timestamp(halt_date_value)
        halted_keys = {(halted_date, halt_ticker)}
        published_daily = published_daily[
            ~(
                pd.to_datetime(published_daily["date"]).eq(halted_date)
                & published_daily["ticker"].eq(halt_ticker)
            )
        ].copy()
    state_source = complete_daily.copy()
    if dpv_only_key is not None:
        dpv_only_date, dpv_only_ticker = dpv_only_key
        state_source = state_source[
            ~(
                pd.to_datetime(state_source["date"]).eq(pd.Timestamp(dpv_only_date))
                & state_source["ticker"].eq(dpv_only_ticker)
            )
        ].copy()
    _publish(root, "daily_price_volume", published_daily)
    _publish_daily_market_state(
        root,
        state_source,
        pd.Timestamp("2025-01-31"),
        run_end,
        halted_without_price=halted_keys,
    )
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
    stale.metadata["market_state_identity"]["manifest_sha256"] = "stale"
    stale.metadata["spec_hash"] = "stale"
    stale_report = lab.validate(stale)
    assert stale_report.status == "NOT_READY"
    assert {issue.code for issue in stale_report.hard_failures}.issuperset(
        {"source_identity_mismatch", "spec_identity_mismatch"}
    )
    assert "market_state_identity_mismatch" in {
        issue.code for issue in stale_report.hard_failures
    }

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
    prepared_ids: list[int] = []
    original = PortfolioExecutionEngine.prepare_market
    original_run = PortfolioExecutionEngine.run

    def recording_prepare_market(market):
        calls.append(len(market))
        return original(market)

    def recording_run(
        self, spec, targets, market, calendar, initial_capital, *, security_master=None
    ):
        prepared_ids.append(id(market))
        return original_run(
            self,
            spec,
            targets,
            market,
            calendar,
            initial_capital,
            security_master=security_master,
        )

    monkeypatch.setattr(
        PortfolioExecutionEngine,
        "prepare_market",
        staticmethod(recording_prepare_market),
    )
    monkeypatch.setattr(PortfolioExecutionEngine, "run", recording_run)

    ETFTrickLab.from_data_analysts(tmp_path).run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    assert len(calls) == 1
    assert len(prepared_ids) == len(ETF_IDS) == 13
    assert len(set(prepared_ids)) == 1


def test_run_all_prepares_one_universe_context_per_formation(tmp_path, monkeypatch):
    start, end = _data_analysts_fixture(tmp_path)
    calls: list[pd.Timestamp] = []
    original = UniverseEngine.prepare

    def recording_prepare(
        self, formation_date, features, security_master, ix0001, market_state
    ):
        calls.append(pd.Timestamp(formation_date))
        return original(
            self, formation_date, features, security_master, ix0001, market_state
        )

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


def test_run_all_preserves_state_only_halted_rows_for_execution_and_amount(
    tmp_path, monkeypatch
):
    start, end = _data_analysts_fixture(
        tmp_path, state_only_halt_key=("2025-02-05", "1101")
    )
    monkeypatch.setattr(lab_module, "ETF_IDS", ("momentum",))
    lab = ETFTrickLab.from_data_analysts(tmp_path)
    seen: dict[str, object] = {"scan_calls": 0}
    original_scan = lab.gateway.scan_market_state
    original_prepare = PortfolioExecutionEngine.prepare_market
    original_attach = lab_module.attach_etf_amount

    def recording_scan(*args, **kwargs):
        state = original_scan(*args, **kwargs)
        seen["scan_calls"] = int(seen["scan_calls"]) + 1
        seen["state"] = state
        return state

    def recording_prepare(market):
        seen["execution_market"] = market.copy()
        return original_prepare(market)

    def recording_attach(daily_etf, holdings, market_state, security_master):
        seen["amount_uses_same_state"] = market_state is seen["state"]
        return original_attach(daily_etf, holdings, market_state, security_master)

    monkeypatch.setattr(lab.gateway, "scan_market_state", recording_scan)
    monkeypatch.setattr(
        PortfolioExecutionEngine,
        "prepare_market",
        staticmethod(recording_prepare),
    )
    monkeypatch.setattr(lab_module, "attach_etf_amount", recording_attach)

    result = lab.run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    halted_date = pd.Timestamp("2025-02-05")
    execution_market = seen["execution_market"]
    halted_execution = execution_market[
        execution_market["date"].eq(halted_date)
        & execution_market["ticker"].eq("1101")
    ]
    assert seen["scan_calls"] == 1
    assert seen["amount_uses_same_state"] is True
    assert len(halted_execution) == 1
    assert halted_execution["market_state"].eq("HALTED").all()
    assert halted_execution["exchange_tradable"].eq(False).all()
    assert halted_execution[["close", "adj_close", "traded_value"]].isna().all().all()

    halted_trades = result.trades[
        result.trades["date"].eq(halted_date)
        & result.trades["ticker"].eq("1101")
    ]
    assert not halted_trades.empty
    assert halted_trades["executed_shares"].eq(0).all()
    prior_holdings = result.daily_holdings[
        result.daily_holdings["date"].eq(pd.Timestamp("2025-02-04"))
    ]
    prior_holding = prior_holdings[prior_holdings["ticker"].eq("1101")].iloc[0]
    halted_holding = result.daily_holdings[
        result.daily_holdings["date"].eq(halted_date)
        & result.daily_holdings["ticker"].eq("1101")
    ]
    assert len(halted_holding) == 1
    assert halted_holding.iloc[0]["shares"] == prior_holding["shares"]
    assert halted_holding.iloc[0]["source_price_date"] == pd.Timestamp("2025-02-04")
    assert halted_holding.iloc[0]["stale_price_days"] == 1
    assert halted_holding.iloc[0]["raw_close"] == pytest.approx(
        prior_holding["raw_close"]
    )

    amount_row = result.daily_etf[result.daily_etf["date"].eq(halted_date)].iloc[0]
    assert amount_row["status_zero_authorized_count"] == 1
    assert amount_row["status_missing_count"] == 0
    assert amount_row["amount_quality_state"] == "READY"


def test_run_all_rejects_requested_scope_dpv_key_without_certified_state(
    tmp_path, monkeypatch
):
    start, end = _data_analysts_fixture(
        tmp_path, dpv_only_key=("2025-02-05", "IX0001")
    )

    def reject_late_calculation(*args, **kwargs):
        raise AssertionError("feature calculation ran before state coverage gate")

    monkeypatch.setattr(PITFeatureEngine, "compute_many", reject_late_calculation)

    with pytest.raises(ValueError, match="daily_price_volume.*without certified"):
        ETFTrickLab.from_data_analysts(tmp_path).run_all(
            start_date=start,
            end_date=end,
            initial_capital=Decimal("1234567"),
        )


def test_run_all_records_state_lineage_and_bounded_round_trip(tmp_path, monkeypatch):
    start, end = _data_analysts_fixture(tmp_path)
    lab = ETFTrickLab.from_data_analysts(tmp_path)
    seen: dict[str, object] = {"scan_calls": 0, "prepared_ids": []}
    original_scan = lab.gateway.scan_market_state
    original_run = PortfolioExecutionEngine.run
    original_attach = lab_module.attach_etf_amount

    def recording_scan(*args, **kwargs):
        seen["scan_calls"] = int(seen["scan_calls"]) + 1
        seen["scan_bounds"] = tuple(pd.Timestamp(value) for value in args[:2])
        state = original_scan(*args, **kwargs)
        seen["state"] = state
        return state

    def recording_run(
        self, spec, targets, market, calendar, initial_capital, *, security_master=None
    ):
        seen["prepared_ids"].append(id(market))
        return original_run(
            self,
            spec,
            targets,
            market,
            calendar,
            initial_capital,
            security_master=security_master,
        )

    def recording_attach(daily_etf, holdings, market_state, security_master):
        seen["amount_uses_same_state"] = market_state is seen["state"]
        return original_attach(daily_etf, holdings, market_state, security_master)

    monkeypatch.setattr(lab.gateway, "scan_market_state", recording_scan)
    monkeypatch.setattr(PortfolioExecutionEngine, "run", recording_run)
    monkeypatch.setattr(lab_module, "attach_etf_amount", recording_attach)

    result = lab.run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    state_manifest_path = (
        tmp_path / "data_store/manifests/daily_market_state.json"
    )
    expected_manifest_hash = hashlib.sha256(
        state_manifest_path.read_bytes()
    ).hexdigest()
    assert seen["scan_calls"] == 1
    assert seen["scan_bounds"] == (
        pd.Timestamp("2025-01-31"),
        pd.Timestamp("2025-02-07"),
    )
    assert len(seen["prepared_ids"]) == len(ETF_IDS) == 13
    assert len(set(seen["prepared_ids"])) == 1
    assert seen["amount_uses_same_state"] is True
    assert result.metadata["manifest_hashes"]["daily_market_state"] == expected_manifest_hash
    assert result.metadata["market_state_identity"] == {
        "artifact_id": "daily_market_state",
        "manifest_sha256": expected_manifest_hash,
        "active_version": "market-state-v3",
        "classification_policy_version": "daily_market_state_v3",
        "state_lattice_policy_version": "daily_market_state_lattice_v5",
        "market_identity_policy_version": "daily_market_identity_v3",
        "dependency_certification_fingerprint": "b" * 64,
    }
    assert result.metadata["market_state_config"] == {
        "scan_start_date": "2025-01-31",
        "scan_end_date": "2025-02-07",
        "formation_admission": "TRADING_ONLY",
        "execution_admission": "SAME_SESSION_TRADING_AND_EXCHANGE_TRADABLE",
        "amount_source": "PRIOR_SESSION_HOLDINGS_AUTHORITATIVE_TRADED_VALUE",
    }
    lifecycle = result.metadata["lifecycle_diagnostics"]
    assert lifecycle["lifecycle_conflict_count"] == 0
    assert lifecycle["identity_conflict_count"] == 0
    assert lifecycle["formation_exclusion_reason_counts"] == {}

    output = tmp_path / "bounded-result"
    handle = result.write(output)
    restored = ETFTrickResult.read(
        output,
        expected_handle=handle,
    )
    for name in (
        "daily_etf",
        "daily_holdings",
        "trades",
        "monthly_targets",
        "candidate_audit",
        "diagnostics",
    ):
        pd.testing.assert_frame_equal(getattr(restored, name), getattr(result, name))
        assert len(handle["tables"][name]["sha256"]) == 64
    assert restored.metadata == result.metadata
    assert restored.result_manifest_sha256 == handle.manifest_sha256
    assert lab.validate(restored).status == "READY"


def test_run_all_accepts_certified_market_state_generation_rotation(tmp_path):
    start, end = _data_analysts_fixture(tmp_path)
    manifest_path = (
        tmp_path / "data_store" / "manifests" / "daily_market_state.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rotated_identity = {
        "active_version": "market-state-v4",
        "state_lattice_policy_version": "daily_market_state_lattice_v6",
        "market_identity_policy_version": "daily_market_identity_v4",
        "dependency_certification_fingerprint": "c" * 64,
    }
    manifest.update(rotated_identity)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    lab = ETFTrickLab.from_data_analysts(tmp_path)
    result = lab.run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )
    assert {
        key: result.metadata["market_state_identity"][key]
        for key in rotated_identity
    } == rotated_identity
    assert lab.validate(result).status == "READY"

    handle = result.write(tmp_path / "rotated-result")
    restored = ETFTrickResult.read(
        tmp_path / "rotated-result", expected_handle=handle
    )
    assert lab.validate(restored).status == "READY"

    stale_generation = copy.deepcopy(restored)
    stale_generation.metadata["market_state_identity"][
        "state_lattice_policy_version"
    ] = "daily_market_state_lattice_v7"
    stale_report = lab.validate(stale_generation)
    assert stale_report.status == "NOT_READY"
    assert "market_state_identity_mismatch" in {
        issue.code for issue in stale_report.hard_failures
    }


def test_validate_rejects_non_string_market_state_generation_even_if_coerced_identity_matches(
    tmp_path,
):
    start, end = _data_analysts_fixture(tmp_path)
    lab = ETFTrickLab.from_data_analysts(tmp_path)
    result = lab.run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    manifest_path = (
        tmp_path / "data_store" / "manifests" / "daily_market_state.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["active_version"] = 4
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    result.metadata["manifest_hashes"]["daily_market_state"] = manifest_sha256
    result.metadata["market_state_identity"].update(
        {
            "manifest_sha256": manifest_sha256,
            "active_version": "4",
        }
    )

    with pytest.raises(DataContractError, match="active_version"):
        lab.validate(result)


def test_validate_rejects_hybrid_identity_when_market_state_rotates_mid_validation(
    tmp_path, monkeypatch
):
    start, end = _data_analysts_fixture(tmp_path)
    lab = ETFTrickLab.from_data_analysts(tmp_path)
    result = lab.run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    manifest_path = (
        tmp_path / "data_store" / "manifests" / "daily_market_state.json"
    )
    manifest_a = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_b = copy.deepcopy(manifest_a)
    manifest_b.update(
        {
            "active_version": "market-state-v4",
            "state_lattice_policy_version": "daily_market_state_lattice_v6",
            "market_identity_policy_version": "daily_market_identity_v4",
            "dependency_certification_fingerprint": "c" * 64,
        }
    )
    manifest_a_bytes = json.dumps(
        manifest_a, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    manifest_b_bytes = json.dumps(
        manifest_b, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    manifest_a_sha256 = hashlib.sha256(manifest_a_bytes).hexdigest()

    result.metadata["manifest_hashes"][
        "daily_market_state"
    ] = manifest_a_sha256
    result.metadata["market_state_identity"].update(
        {
            "manifest_sha256": manifest_a_sha256,
            "active_version": manifest_b["active_version"],
            "state_lattice_policy_version": manifest_b[
                "state_lattice_policy_version"
            ],
            "market_identity_policy_version": manifest_b[
                "market_identity_policy_version"
            ],
            "dependency_certification_fingerprint": manifest_b[
                "dependency_certification_fingerprint"
            ],
        }
    )

    original_read_manifest_bytes = lab.gateway._read_manifest_bytes
    daily_reads = 0

    def rotating_manifest_bytes(artifact_id: str) -> bytes:
        nonlocal daily_reads
        if artifact_id != "daily_market_state":
            return original_read_manifest_bytes(artifact_id)
        daily_reads += 1
        return manifest_a_bytes if daily_reads == 1 else manifest_b_bytes

    monkeypatch.setattr(
        lab.gateway, "_read_manifest_bytes", rotating_manifest_bytes
    )

    report = lab.validate(result)
    assert report.status == "NOT_READY"
    assert "market_state_authority_drift" in {
        issue.code for issue in report.hard_failures
    }


def test_strict_result_read_rejects_metadata_tampering(tmp_path):
    start, end = _data_analysts_fixture(tmp_path)
    result = ETFTrickLab.from_data_analysts(tmp_path).run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )
    output = tmp_path / "strict-result"
    handle = result.write(output)
    manifest_path = output / "result_manifest.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))

    for metadata_key in ("market_state_config", "lifecycle_diagnostics"):
        tampered = copy.deepcopy(original)
        if metadata_key == "market_state_config":
            tampered["metadata"][metadata_key]["formation_admission"] = "HALTED_ALLOWED"
        else:
            tampered["metadata"][metadata_key]["state_row_count"] += 1
        manifest_path.write_bytes(_canonical_json_bytes(tampered))
        with pytest.raises(ValueError, match="result manifest hash mismatch"):
            ETFTrickResult.read(
                output,
                expected_handle=handle,
            )

    self_endorsed = copy.deepcopy(original)
    self_endorsed["metadata"]["market_state_config"][
        "formation_admission"
    ] = "HALTED_ALLOWED"
    self_endorsed["metadata_sha256"] = hashlib.sha256(
        _canonical_json_bytes(self_endorsed["metadata"])
    ).hexdigest()
    manifest_path.write_bytes(_canonical_json_bytes(self_endorsed))
    with pytest.raises(ValueError, match="result manifest hash mismatch"):
        ETFTrickResult.read(
            output,
            expected_handle=handle,
        )


def test_strict_result_read_rejects_ungoverned_policy_with_new_digest(tmp_path):
    start, end = _data_analysts_fixture(tmp_path)
    result = ETFTrickLab.from_data_analysts(tmp_path).run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )
    output = tmp_path / "policy-result"
    original_handle = result.write(output)
    manifest_path = output / "result_manifest.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))

    cases: list[tuple[str, dict[str, object]]] = []
    wrong_config = copy.deepcopy(original)
    wrong_config["metadata"]["market_state_config"][
        "formation_admission"
    ] = "HALTED_ALLOWED"
    cases.append(("market_state_config", wrong_config))
    extra_config = copy.deepcopy(original)
    extra_config["metadata"]["market_state_config"]["fallback"] = "DPV"
    cases.append(("market_state_config", extra_config))
    missing_config = copy.deepcopy(original)
    del missing_config["metadata"]["market_state_config"]["amount_source"]
    cases.append(("market_state_config", missing_config))
    wrong_identity = copy.deepcopy(original)
    wrong_identity["metadata"]["market_state_identity"][
        "classification_policy_version"
    ] = "daily_market_state_v2"
    cases.append(("market_state_identity", wrong_identity))
    extra_identity = copy.deepcopy(original)
    extra_identity["metadata"]["market_state_identity"]["fallback"] = "DPV"
    cases.append(("market_state_identity", extra_identity))
    empty_generation = copy.deepcopy(original)
    empty_generation["metadata"]["market_state_identity"]["active_version"] = ""
    cases.append(("market_state_identity", empty_generation))
    malformed_fingerprint = copy.deepcopy(original)
    malformed_fingerprint["metadata"]["market_state_identity"][
        "dependency_certification_fingerprint"
    ] = "certification-v1"
    cases.append(("market_state_identity", malformed_fingerprint))
    wrong_lifecycle = copy.deepcopy(original)
    wrong_lifecycle["metadata"]["lifecycle_diagnostics"]["state_row_count"] += 1
    cases.append(("lifecycle diagnostics", wrong_lifecycle))

    for expected_message, tampered in cases:
        tampered["metadata_sha256"] = hashlib.sha256(
            _canonical_json_bytes(tampered["metadata"])
        ).hexdigest()
        raw = _canonical_json_bytes(tampered)
        manifest_path.write_bytes(raw)
        tampered_handle = replace(
            original_handle,
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
            market_state_identity_sha256=hashlib.sha256(
                _canonical_json_bytes(
                    tampered["metadata"]["market_state_identity"]
                )
            ).hexdigest(),
        )
        with pytest.raises(ValueError, match=expected_message):
            ETFTrickResult.read(
                output,
                expected_handle=tampered_handle,
            )


def test_validate_rejects_in_memory_state_metadata_tampering(tmp_path):
    start, end = _data_analysts_fixture(tmp_path)
    lab = ETFTrickLab.from_data_analysts(tmp_path)
    result = lab.run_all(
        start_date=start,
        end_date=end,
        initial_capital=Decimal("1234567"),
    )

    config_tampered = copy.deepcopy(result)
    config_tampered.metadata["market_state_config"][
        "formation_admission"
    ] = "HALTED_ALLOWED"
    config_report = lab.validate(config_tampered)
    assert config_report.status == "NOT_READY"
    assert "market_state_metadata_mismatch" in {
        issue.code for issue in config_report.hard_failures
    }

    lifecycle_tampered = copy.deepcopy(result)
    lifecycle_tampered.metadata["lifecycle_diagnostics"]["state_row_count"] += 1
    lifecycle_report = lab.validate(lifecycle_tampered)
    assert lifecycle_report.status == "NOT_READY"
    assert "lifecycle_diagnostics_mismatch" in {
        issue.code for issue in lifecycle_report.hard_failures
    }


def test_run_all_rejects_missing_physical_state_end_before_feature_calculation(
    tmp_path, monkeypatch
):
    start, end = _data_analysts_fixture(tmp_path)
    parquet_path = (
        tmp_path
        / "data_store/canonical/derived/daily_market_state/year=2025/part.parquet"
    )
    state = pd.read_parquet(parquet_path)
    _rewrite_market_state_rows(
        tmp_path,
        state[pd.to_datetime(state["date"]).lt(end)].copy(),
    )

    def reject_late_calculation(*args, **kwargs):
        raise AssertionError("feature calculation ran before state coverage gate")

    monkeypatch.setattr(PITFeatureEngine, "compute_many", reject_late_calculation)
    with pytest.raises(ValueError, match="daily_market_state physical coverage"):
        ETFTrickLab.from_data_analysts(tmp_path).run_all(
            start_date=start,
            end_date=end,
            initial_capital=Decimal("1234567"),
        )


def test_run_all_rejects_stale_state_manifest_before_feature_calculation(
    tmp_path, monkeypatch
):
    start, end = _data_analysts_fixture(tmp_path)
    manifest_path = tmp_path / "data_store/manifests/daily_market_state.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["build_end"] = "2025-02-06"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def reject_late_calculation(*args, **kwargs):
        raise AssertionError("feature calculation ran before state coverage gate")

    monkeypatch.setattr(PITFeatureEngine, "compute_many", reject_late_calculation)
    with pytest.raises(DataContractError, match="outside governed build coverage"):
        ETFTrickLab.from_data_analysts(tmp_path).run_all(
            start_date=start,
            end_date=end,
            initial_capital=Decimal("1234567"),
        )
