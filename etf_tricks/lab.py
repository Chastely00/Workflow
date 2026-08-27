from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pandas as pd

from .allocation import AllocationPlan, AllocationPlanner
from .calendar import TradingCalendar
from .data_gateway import DataGateway
from .execution import PortfolioExecutionEngine
from .features import PITFeatureEngine
from .registry import ETF_IDS, get_etf_spec
from .result import ETFTrickResult, attach_etf_amount
from .universe import UniverseEngine
from .validation import (
    ReadinessReport,
    ValidationIssue,
    build_selection_diagnostics,
    validate_result,
)


class ETFTrickLab:
    def __init__(self, gateway: DataGateway) -> None:
        self.gateway = gateway
        self._last_result: ETFTrickResult | None = None

    @classmethod
    def from_data_analysts(cls, root: str | Path | None = None) -> "ETFTrickLab":
        resolved = Path(root) if root is not None else Path.cwd() / "DataAnalysts"
        return cls(DataGateway.from_data_analysts(resolved))

    def run_all(
        self,
        *,
        start_date: str | pd.Timestamp,
        end_date: str | pd.Timestamp,
        initial_capital: int | float | Decimal = Decimal("10000000"),
    ) -> ETFTrickResult:
        start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
        if start > end:
            raise ValueError("start_date must be on or before end_date")

        calendar_frame = self.gateway.read_artifact(
            "trading_calendar", columns=["date", "market", "is_trading_day"]
        )
        full_calendar = TradingCalendar(calendar_frame)
        self._assert_calendar_covers(full_calendar, start, end)
        run_days = full_calendar.trading_days(start, end)
        if not run_days:
            raise ValueError("requested interval has no TRADEDAY_TWSE dates")
        run_calendar = TradingCalendar(
            pd.DataFrame(
                {"date": run_days, "market": "TWSE", "is_trading_day": True}
            )
        )
        formation_dates = self._formation_dates_for_run(full_calendar, start, end)
        if formation_dates:
            latest_formation = max(formation_dates)
            self._assert_sparse_source_fresh("monthly_sales", latest_formation, 45)
            self._assert_sparse_source_fresh(
                "financial_statement_raw", latest_formation, 120
            )

        calendar_days = pd.DatetimeIndex(full_calendar.days)
        earliest_formation_position = int(
            calendar_days.get_loc(min(formation_dates))
        )
        feature_warmup_start = calendar_days[
            max(0, earliest_formation_position - 252)
        ]

        daily_columns = [
            "date", "ticker", "close", "adj_close", "volume", "traded_value",
            "turnover", "market_cap",
        ]
        daily = self.gateway.read_artifact(
            "daily_price_volume", columns=daily_columns, start=feature_warmup_start, end=end
        )
        chip = self.gateway.read_artifact(
            "daily_chip",
            columns=["date", "ticker", "qfii_examt", "fund_examt", "dlrp_examt"],
            start=feature_warmup_start,
            end=end,
        )
        sales = self.gateway.read_artifact(
            "monthly_sales",
            columns=[
                "ticker", "r18", "source_period_date", "source_available_date",
                "source_row_id",
            ],
        )
        financial = self.gateway.read_artifact(
            "financial_statement_raw",
            columns=[
                "ticker", "r103", "no", "merg", "curr", "period_end_date",
                "source_available_date", "revision_date", "source_row_id",
            ],
        )
        security_master = self.gateway.read_artifact(
            "security_master",
            columns=["ticker", "stock_name", "list_date", "delist_date", "main_industry"],
        )

        feature_engine = PITFeatureEngine(
            full_calendar,
            {
                "daily_price_volume": daily,
                "daily_chip": chip,
                "monthly_sales": sales,
                "financial_statement_raw": financial,
            },
        )
        universe_engine = UniverseEngine(full_calendar)
        ix0001 = (
            daily[daily["ticker"].astype(str).eq("IX0001")]
            .loc[:, ["date", "ticker", "traded_value"]]
            .rename(columns={"traded_value": "amt"})
        )

        daily_start = pd.to_datetime(daily["date"], errors="coerce").min()
        ix_dates = pd.DatetimeIndex(
            pd.to_datetime(ix0001["date"], errors="coerce").dropna().sort_values().unique()
        )
        if len(ix_dates) < 20:
            raise ValueError("IX0001 has fewer than 20 observations in available coverage")
        liquidity_warmup_end = ix_dates[19]
        formation_dates = tuple(
            formation
            for formation in formation_dates
            if formation >= daily_start and formation >= liquidity_warmup_end
        )
        targets_by_etf: dict[str, list[pd.DataFrame]] = {etf_id: [] for etf_id in ETF_IDS}
        candidate_frames: list[pd.DataFrame] = []
        features_by_date = feature_engine.compute_many(formation_dates)
        for formation in formation_dates:
            features = features_by_date[formation]
            for etf_id in ETF_IDS:
                spec = get_etf_spec(etf_id)
                selection = universe_engine.select(
                    spec, formation, features, security_master, ix0001
                )
                candidate_frames.append(selection.candidates)
                if selection.targets.empty:
                    continue
                target = selection.targets.copy()
                target["target_month"] = formation.to_period("M") + 1
                target["rank"] = range(1, len(target) + 1)
                target["signal_name"] = spec.signal_name
                targets_by_etf[etf_id].append(target)

        execution_market = daily[
            daily["date"].between(run_days[0], run_days[-1])
        ].copy()
        engine = PortfolioExecutionEngine()
        prepared_execution_market = engine.prepare_market(execution_market)
        engine_tables = []
        target_outputs = []
        for etf_id in ETF_IDS:
            target = self._concat(targets_by_etf[etf_id])
            target_outputs.append(target)
            engine_tables.append(
                engine.run(
                    get_etf_spec(etf_id),
                    target,
                    prepared_execution_market,
                    run_calendar,
                    Decimal(str(initial_capital)),
                    security_master=security_master,
                )
            )

        daily_etf = self._concat([table.daily_etf for table in engine_tables])
        holdings = self._concat([table.daily_holdings for table in engine_tables])
        trades = self._concat([table.trades for table in engine_tables])
        candidate_output = self._concat(candidate_frames)
        diagnostics = self._concat(
            [
                *[table.diagnostics for table in engine_tables],
                build_selection_diagnostics(candidate_output),
            ]
        )
        daily_etf = attach_etf_amount(daily_etf, holdings, execution_market)
        result = ETFTrickResult(
            daily_etf=daily_etf,
            daily_holdings=holdings,
            trades=trades,
            monthly_targets=self._concat(target_outputs),
            candidate_audit=candidate_output,
            diagnostics=diagnostics,
            metadata={
                "run_config": {
                    "start_date": str(start.date()),
                    "end_date": str(end.date()),
                    "initial_capital": str(initial_capital),
                },
                "manifest_hashes": self._manifest_hashes(),
                "spec_hash": self._spec_hash(),
            },
        )
        self._last_result = result
        return result

    def allocate(
        self,
        *,
        etf_id: str,
        as_of_date: str | pd.Timestamp,
        capital: int | float | Decimal,
    ) -> AllocationPlan:
        targets, prices, execution_dates = self._allocation_context(etf_id, as_of_date)
        return AllocationPlanner().allocate(
            etf_id,
            as_of_date,
            targets,
            prices,
            execution_dates,
            Decimal(str(capital)),
        )

    def rebalance(
        self,
        *,
        etf_id: str,
        as_of_date: str | pd.Timestamp,
        current_positions: dict[str, int],
        current_cash: int | float | Decimal,
        capital_delta: int | float | Decimal,
    ) -> AllocationPlan:
        targets, prices, execution_dates = self._allocation_context(
            etf_id, as_of_date, extra_tickers=current_positions
        )
        return AllocationPlanner().rebalance(
            etf_id,
            as_of_date,
            targets,
            prices,
            execution_dates,
            current_positions,
            Decimal(str(current_cash)),
            Decimal(str(capital_delta)),
        )

    def validate(self, result: ETFTrickResult | None = None) -> ReadinessReport:
        selected = result or self._last_result
        if selected is None:
            raise RuntimeError("run_all must complete before validate")
        config = selected.metadata.get("run_config", {})
        start, end = config.get("start_date"), config.get("end_date")
        if start is None or end is None:
            raise ValueError("result metadata lacks run_config date bounds")
        calendar_frame = self.gateway.read_artifact(
            "trading_calendar", columns=["date", "market", "is_trading_day"]
        )
        full_validation_calendar = TradingCalendar(calendar_frame)
        self._assert_calendar_covers(
            full_validation_calendar, pd.Timestamp(start), pd.Timestamp(end)
        )
        calendar_frame = calendar_frame[
            pd.to_datetime(calendar_frame["date"]).between(start, end)
        ]
        validation_calendar = TradingCalendar(calendar_frame)
        report = validate_result(selected, validation_calendar, ETF_IDS)
        identity_failures: list[ValidationIssue] = []
        if selected.metadata.get("manifest_hashes") != self._manifest_hashes():
            identity_failures.append(
                ValidationIssue(
                    "source_identity_mismatch",
                    "result source manifest hashes do not match the current DataAnalysts artifacts",
                )
            )
        if selected.metadata.get("spec_hash") != self._spec_hash():
            identity_failures.append(
                ValidationIssue(
                    "spec_identity_mismatch",
                    "result ETF specification hash does not match the current governed registry",
                )
            )
        expected_days = pd.DatetimeIndex(validation_calendar.days)
        observed_days = pd.DatetimeIndex(
            pd.to_datetime(selected.daily_etf.get("date"), errors="coerce")
            .dropna()
            .unique()
        ).sort_values()
        if (
            len(expected_days) == 0
            or len(observed_days) == 0
            or len(observed_days.difference(expected_days)) > 0
            or observed_days.max() != expected_days.max()
        ):
            identity_failures.append(
                ValidationIssue(
                    "run_bounds_mismatch",
                    "daily ETF dates do not equal the governed trading days in run_config bounds",
                )
            )
        if not identity_failures:
            return report
        hard = (*report.hard_failures, *identity_failures)
        return replace(
            report,
            status="NOT_READY",
            hard_failures=hard,
            目前可用=(),
            目前缺失限制=tuple(
                issue.message for issue in (*hard, *report.warnings)
            ),
        )

    def _allocation_context(
        self,
        etf_id: str,
        as_of_date: str | pd.Timestamp,
        *,
        extra_tickers: object = (),
    ) -> tuple[pd.DataFrame, pd.DataFrame, tuple[pd.Timestamp, ...]]:
        if etf_id not in ETF_IDS:
            raise KeyError(f"unknown ETF ID: {etf_id}")
        if self._last_result is None:
            raise RuntimeError("run_all must complete before allocate or rebalance")
        as_of = pd.Timestamp(as_of_date)
        target_history = self._last_result.monthly_targets
        eligible = target_history[
            target_history["etf_id"].eq(etf_id)
            & pd.to_datetime(target_history["formation_date"]).le(as_of)
        ]
        if eligible.empty:
            raise RuntimeError(f"no governed target is available for {etf_id} by {as_of.date()}")
        formation = pd.to_datetime(eligible["formation_date"]).max()
        if as_of != formation:
            raise ValueError(
                f"as_of_date must equal the governed formation_date {formation.date()}"
            )
        governed = eligible[pd.to_datetime(eligible["formation_date"]).eq(formation)].copy()
        target_months = pd.PeriodIndex(governed["target_month"], freq="M").unique()
        if len(target_months) != 1:
            raise ValueError("governed target must contain exactly one target_month")
        targets = governed[["ticker", "stock_name", "target_weight"]].copy()
        requested_tickers = set(targets["ticker"].astype(str)) | {
            str(ticker) for ticker in extra_tickers
        }
        price_frame = self.gateway.read_artifact(
            "daily_price_volume",
            columns=["date", "ticker", "close"],
            start=formation,
            end=formation,
        )
        price_frame = price_frame[
            price_frame["ticker"].astype(str).isin(requested_tickers)
        ].rename(columns={"close": "raw_close"})
        calendar = TradingCalendar(
            self.gateway.read_artifact(
                "trading_calendar", columns=["date", "market", "is_trading_day"]
            )
        )
        execution_dates = calendar.month(target_months[0].start_time)
        return targets, price_frame[["ticker", "raw_close"]], execution_dates

    @staticmethod
    def _formation_dates_for_run(
        calendar: TradingCalendar, start: pd.Timestamp, end: pd.Timestamp
    ) -> tuple[pd.Timestamp, ...]:
        first_target_month = start.to_period("M")
        last_target_month = end.to_period("M")
        values = []
        for target_month in pd.period_range(first_target_month, last_target_month, freq="M"):
            formation_month = target_month - 1
            values.append(calendar.month_end(formation_month.start_time))
        return tuple(values)

    @staticmethod
    def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
        nonempty = [frame for frame in frames if not frame.empty]
        return pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()

    def _manifest_hashes(self) -> dict[str, str]:
        artifact_ids = (
            "trading_calendar", "daily_price_volume", "daily_chip", "monthly_sales",
            "financial_statement_raw", "security_master",
        )
        hashes = {}
        for artifact_id in artifact_ids:
            manifest = self.gateway.load_manifest(artifact_id)
            payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
            hashes[artifact_id] = hashlib.sha256(payload).hexdigest()
        return hashes

    def _assert_sparse_source_fresh(
        self, artifact_id: str, formation_date: pd.Timestamp, max_staleness_days: int
    ) -> None:
        manifest = self.gateway.load_manifest(artifact_id)
        raw_range = manifest.get("availability_date_range") or manifest.get("date_range")
        if not isinstance(raw_range, list) or len(raw_range) != 2:
            raise ValueError(f"{artifact_id} manifest lacks availability coverage")
        upper = pd.Timestamp(raw_range[1])
        age = (formation_date - upper).days
        if age > max_staleness_days:
            raise ValueError(
                f"{artifact_id} availability coverage is {age} days stale at formation_date "
                f"{formation_date.date()}, limit={max_staleness_days}"
            )

    @staticmethod
    def _assert_calendar_covers(
        calendar: TradingCalendar, start: pd.Timestamp, end: pd.Timestamp
    ) -> None:
        days = pd.DatetimeIndex(calendar.days)
        if len(days) == 0 or days.min() > start or days.max() < end:
            raise ValueError(
                f"trading_calendar physical coverage does not contain requested bounds "
                f"{start.date()}..{end.date()}"
            )

    @staticmethod
    def _spec_hash() -> str:
        payload = [get_etf_spec(etf_id).__dict__ for etf_id in ETF_IDS]
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
