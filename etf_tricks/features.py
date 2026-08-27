from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

from .calendar import TradingCalendar


class FeatureContractError(RuntimeError):
    pass


_DAILY_COLUMNS = {
    "date",
    "ticker",
    "close",
    "adj_close",
    "volume",
    "traded_value",
    "turnover",
    "market_cap",
}
_CHIP_COLUMNS = {
    "date",
    "ticker",
    "qfii_examt",
    "fund_examt",
    "dlrp_examt",
}


class PITFeatureEngine:
    def __init__(
        self,
        calendar: TradingCalendar,
        panels: Mapping[str, pd.DataFrame],
    ) -> None:
        self.calendar = calendar
        self.daily = self._normalize_daily(panels.get("daily_price_volume"))
        self.chip = self._normalize_chip(panels.get("daily_chip"))
        self.monthly_sales = self._normalize_dates(
            panels.get("monthly_sales", pd.DataFrame()).copy(),
            ("source_period_date", "source_available_date"),
        )
        self.financial = self._normalize_dates(
            panels.get("financial_statement_raw", pd.DataFrame()).copy(),
            ("period_end_date", "source_available_date", "revision_date"),
        )
        self._daily_by_date = self.daily.groupby("date", sort=False)
        self._daily_by_ticker = {
            str(ticker): group.set_index("date")
            for ticker, group in self.daily.groupby("ticker", sort=False)
        }
        self._chip_by_ticker = {
            str(ticker): group.set_index("date")
            for ticker, group in self.chip.groupby("ticker", sort=False)
        }
        self._empty_daily_history = self.daily.iloc[:0].set_index("date")
        self._empty_chip_history = self.chip.iloc[:0].set_index("date")

    def compute(self, formation_date: str | pd.Timestamp) -> pd.DataFrame:
        formation = pd.Timestamp(formation_date)
        return self.compute_many((formation,))[formation]

    def _compute_scalar(self, formation_date: str | pd.Timestamp) -> pd.DataFrame:
        formation = pd.Timestamp(formation_date)
        days = pd.DatetimeIndex(self.calendar.days)
        positions = np.flatnonzero(days == formation)
        if len(positions) != 1:
            raise FeatureContractError(
                f"formation date is not a unique TWSE trading day: {formation.date()}"
            )
        position = int(positions[0])

        try:
            formation_rows = self._daily_by_date.get_group(formation).copy()
        except KeyError:
            formation_rows = self.daily.iloc[:0].copy()
        duplicates = formation_rows[formation_rows.duplicated("ticker", keep=False)]
        if not duplicates.empty:
            tickers = sorted(duplicates["ticker"].unique())
            raise FeatureContractError(
                f"duplicate daily rows at formation date {formation.date()}: {tickers}"
            )
        formation_rows = formation_rows.sort_values("ticker", kind="stable")

        if formation_rows.empty:
            schema: dict[str, object] = {
                "formation_date": formation,
                "ticker": "",
                "close": math.nan,
                "adj_close": math.nan,
                "market_cap": math.nan,
            }
            schema.update(
                self._daily_signals(
                    self._empty_daily_history,
                    self._empty_chip_history,
                    days,
                    position,
                )
            )
            schema.update(self._empty_sales())
            schema.update(self._empty_roe())
            return pd.DataFrame(columns=list(schema))

        sales = self._select_monthly_sales(formation)
        roe = self._select_roe(formation)
        records: list[dict[str, object]] = []
        for base in formation_rows.itertuples(index=False):
            ticker = str(base.ticker)
            history = self._daily_by_ticker.get(ticker, self._empty_daily_history)
            chip_history = self._chip_by_ticker.get(ticker, self._empty_chip_history)
            record: dict[str, object] = {
                "formation_date": formation,
                "ticker": ticker,
                "close": self._finite_or_nan(base.close),
                "adj_close": self._finite_or_nan(base.adj_close),
                "market_cap": self._finite_or_nan(base.market_cap),
            }
            record.update(self._daily_signals(history, chip_history, days, position))
            record.update(sales.get(ticker, self._empty_sales()))
            record.update(roe.get(ticker, self._empty_roe()))
            records.append(record)
        return pd.DataFrame.from_records(records).sort_values(
            "ticker", kind="stable", ignore_index=True
        )

    def compute_many(
        self, formation_dates: tuple[str | pd.Timestamp, ...]
    ) -> dict[pd.Timestamp, pd.DataFrame]:
        """Compute all formation-date snapshots from one bounded dense panel."""
        formations = tuple(pd.Timestamp(value) for value in formation_dates)
        if not formations:
            return {}
        if len(set(formations)) != len(formations):
            raise FeatureContractError("formation dates must be unique")

        days = pd.DatetimeIndex(self.calendar.days)
        absolute_positions = days.get_indexer(formations)
        if (absolute_positions < 0).any():
            invalid = formations[int(np.flatnonzero(absolute_positions < 0)[0])]
            raise FeatureContractError(
                f"formation date is not a unique TWSE trading day: {invalid.date()}"
            )

        warmup_start = max(0, int(absolute_positions.min()) - 252)
        final_position = int(absolute_positions.max())
        panel_days = days[warmup_start : final_position + 1]
        local_positions = absolute_positions - warmup_start

        daily_dates = pd.to_datetime(self.daily["date"], errors="coerce")
        daily_day_codes = panel_days.get_indexer(daily_dates)
        relevant_daily = self.daily.loc[daily_day_codes >= 0].copy()
        relevant_daily["_day_code"] = daily_day_codes[daily_day_codes >= 0]
        duplicate_daily = relevant_daily.duplicated(["date", "ticker"], keep=False)
        if duplicate_daily.any():
            sample = relevant_daily.loc[duplicate_daily, ["date", "ticker"]].iloc[0]
            raise FeatureContractError(
                "duplicate daily rows in feature window at "
                f"{pd.Timestamp(sample['date']).date()}: {sample['ticker']}"
            )

        chip_dates = pd.to_datetime(self.chip["date"], errors="coerce")
        chip_day_codes = panel_days.get_indexer(chip_dates)
        relevant_chip = self.chip.loc[chip_day_codes >= 0].copy()
        relevant_chip["_day_code"] = chip_day_codes[chip_day_codes >= 0]
        duplicate_chip = relevant_chip.duplicated(["date", "ticker"], keep=False)
        if duplicate_chip.any():
            sample = relevant_chip.loc[duplicate_chip, ["date", "ticker"]].iloc[0]
            raise FeatureContractError(
                "duplicate chip rows in feature window at "
                f"{pd.Timestamp(sample['date']).date()}: {sample['ticker']}"
            )

        tickers = pd.Index(
            sorted(
                set(relevant_daily["ticker"].astype(str))
                | set(relevant_chip["ticker"].astype(str))
            ),
            dtype=object,
        )
        shape = (len(panel_days), len(tickers))
        daily_ticker_codes = tickers.get_indexer(relevant_daily["ticker"].astype(str))
        chip_ticker_codes = tickers.get_indexer(relevant_chip["ticker"].astype(str))
        daily_rows = relevant_daily["_day_code"].to_numpy(dtype=np.intp)
        chip_rows = relevant_chip["_day_code"].to_numpy(dtype=np.intp)

        daily_presence = np.zeros(shape, dtype=bool)
        daily_presence[daily_rows, daily_ticker_codes] = True

        def daily_matrix(column: str) -> np.ndarray:
            matrix = np.full(shape, np.nan, dtype=np.float64)
            values = pd.to_numeric(
                relevant_daily[column], errors="coerce"
            ).to_numpy(dtype=np.float64)
            matrix[daily_rows, daily_ticker_codes] = values
            return matrix

        close = daily_matrix("close")
        adjusted = daily_matrix("adj_close")
        volume = daily_matrix("volume")
        traded_value = daily_matrix("traded_value")
        turnover = daily_matrix("turnover")
        market_cap = daily_matrix("market_cap")

        chip_component_matrices = []
        for column in ("qfii_examt", "fund_examt", "dlrp_examt"):
            matrix = np.full(shape, np.nan, dtype=np.float64)
            values = pd.to_numeric(
                relevant_chip[column], errors="coerce"
            ).to_numpy(dtype=np.float64)
            matrix[chip_rows, chip_ticker_codes] = values
            chip_component_matrices.append(matrix)
        chip_components = np.stack(chip_component_matrices, axis=0)

        snapshots: dict[pd.Timestamp, pd.DataFrame] = {}
        for formation, absolute_position, position in zip(
            formations, absolute_positions, local_positions, strict=True
        ):
            selected = np.flatnonzero(daily_presence[position])
            if selected.size == 0:
                snapshots[formation] = self._compute_scalar(formation)
                continue

            selected_tickers = tickers.take(selected)
            last20_start = max(0, int(position) - 19)
            last20 = slice(last20_start, int(position) + 1)
            traded20 = traded_value[last20, selected]
            traded_count = (~np.isnan(traded20)).sum(axis=0)
            turnover20 = turnover[last20, selected]
            turnover_count = (~np.isnan(turnover20)).sum(axis=0)
            with np.errstate(invalid="ignore"):
                traded_sum = np.nansum(traded20, axis=0)
                turnover_sum = np.nansum(turnover20, axis=0)
            traded_mean = np.divide(
                traded_sum,
                traded_count,
                out=np.full(selected.size, np.nan),
                where=traded_count > 0,
            )
            turnover_mean = np.divide(
                turnover_sum,
                turnover_count,
                out=np.full(selected.size, np.nan),
                where=turnover_count > 0,
            )
            traded_mean[traded_count != 20] = np.nan
            traded_sum[traded_count != 20] = np.nan
            turnover_mean[turnover_count != 20] = np.nan

            last80_start = max(0, int(position) - 79)
            volume80 = volume[last80_start : int(position) + 1, selected]
            if volume80.shape[0] == 80:
                denominator = volume80[:60]
                numerator = volume80[60:]
                denominator_count = (~np.isnan(denominator)).sum(axis=0)
                numerator_count = (~np.isnan(numerator)).sum(axis=0)
                with np.errstate(invalid="ignore"):
                    denominator_mean = np.divide(
                        np.nansum(denominator, axis=0),
                        denominator_count,
                        out=np.full(selected.size, np.nan),
                        where=denominator_count > 0,
                    )
                    numerator_mean = np.divide(
                        np.nansum(numerator, axis=0),
                        numerator_count,
                        out=np.full(selected.size, np.nan),
                        where=numerator_count > 0,
                    )
                denominator_mean[denominator_count != 60] = np.nan
                numerator_mean[numerator_count != 20] = np.nan
                with np.errstate(divide="ignore", invalid="ignore"):
                    volume_ratio = numerator_mean / denominator_mean
                volume_ratio[
                    (denominator_count != 60)
                    | (numerator_count != 20)
                    | ~(denominator_mean > 0)
                ] = np.nan
            else:
                denominator_count = np.zeros(selected.size, dtype=np.int64)
                numerator_count = np.zeros(selected.size, dtype=np.int64)
                denominator_mean = np.full(selected.size, np.nan)
                numerator_mean = np.full(selected.size, np.nan)
                volume_ratio = np.full(selected.size, np.nan)

            chip20 = chip_components[
                :, last20_start : int(position) + 1, selected
            ]
            chip_complete = (~np.isnan(chip20)).all(axis=0)
            chip_count = chip_complete.sum(axis=0)
            chip_signal = np.full(selected.size, np.nan)
            if chip20.shape[1] == 20:
                chip_valid = chip_count == 20
                with np.errstate(invalid="ignore"):
                    daily_chip_sum = chip20[:, :, chip_valid].sum(axis=0)
                    chip_signal[chip_valid] = np.nansum(
                        daily_chip_sum,
                        axis=0,
                    )

            if absolute_position >= 252:
                recent_absolute = int(absolute_position) - 21
                old_absolute = int(absolute_position) - 252
                recent_position = recent_absolute - warmup_start
                old_position = old_absolute - warmup_start
                recent_price = adjusted[recent_position, selected].copy()
                old_price = adjusted[old_position, selected].copy()
                recent_price[~np.isfinite(recent_price)] = np.nan
                old_price[~np.isfinite(old_price)] = np.nan
                with np.errstate(divide="ignore", invalid="ignore"):
                    momentum = recent_price / old_price - 1.0
                momentum[(recent_price <= 0) | (old_price <= 0)] = np.nan
                recent_date = days[recent_absolute]
                old_date = days[old_absolute]
            else:
                recent_price = np.full(selected.size, np.nan)
                old_price = np.full(selected.size, np.nan)
                momentum = np.full(selected.size, np.nan)
                recent_date = pd.NaT
                old_date = pd.NaT

            last61_start = max(0, int(position) - 60)
            adjusted61 = adjusted[last61_start : int(position) + 1, selected]
            if adjusted61.shape[0] >= 2:
                with np.errstate(divide="ignore", invalid="ignore"):
                    returns = adjusted61[1:] / adjusted61[:-1] - 1.0
                returns[~np.isfinite(returns)] = np.nan
            else:
                returns = np.empty((0, selected.size), dtype=np.float64)
            return_count = np.isfinite(returns).sum(axis=0)
            returns_frame = pd.DataFrame(returns)
            return_mean = returns_frame.mean(axis=0).to_numpy(dtype=np.float64)
            sample_std = returns_frame.std(axis=0, ddof=1).to_numpy(dtype=np.float64)
            volatility = sample_std * math.sqrt(252)
            volatility[(return_count < 20) | ~(sample_std > 0)] = np.nan
            with np.errstate(divide="ignore", invalid="ignore"):
                sharpe = return_mean / sample_std * math.sqrt(252)
            sharpe[(return_count != 60) | ~(sample_std > 0)] = np.nan
            downside = np.full(selected.size, np.nan)
            complete_returns = return_count == 60
            if complete_returns.any():
                downside[complete_returns] = np.sqrt(
                    np.square(np.minimum(returns[:, complete_returns], 0.0)).mean(axis=0)
                )
            with np.errstate(divide="ignore", invalid="ignore"):
                sortino = return_mean / downside * math.sqrt(252)
            sortino[(return_count != 60) | ~(downside > 0)] = np.nan

            formation_close = close[position, selected].copy()
            formation_adjusted = adjusted[position, selected].copy()
            formation_market_cap = market_cap[position, selected].copy()
            formation_close[~np.isfinite(formation_close)] = np.nan
            formation_adjusted[~np.isfinite(formation_adjusted)] = np.nan
            formation_market_cap[~np.isfinite(formation_market_cap)] = np.nan
            frame = pd.DataFrame(
                {
                    "formation_date": formation,
                    "ticker": selected_tickers.to_numpy(),
                    "close": formation_close,
                    "adj_close": formation_adjusted,
                    "market_cap": formation_market_cap,
                    "adv20_observation_count": traded_count,
                    "adv20": traded_mean,
                    "stock_traded_value_sum20": traded_sum,
                    "turnover_20d_observation_count": turnover_count,
                    "turnover_20d": turnover_mean,
                    "volume_ratio_numerator_count": numerator_count,
                    "volume_ratio_denominator_count": denominator_count,
                    "volume_ratio_numerator_mean": numerator_mean,
                    "volume_ratio_denominator_mean": denominator_mean,
                    "volume_ratio": volume_ratio,
                    "chip_20d_observation_count": chip_count,
                    "chip_20d": chip_signal,
                    "momentum_recent_date": recent_date,
                    "momentum_old_date": old_date,
                    "momentum_recent_adj_close": recent_price,
                    "momentum_old_adj_close": old_price,
                    "momentum_12_1": momentum,
                    "return_60d_observation_count": return_count,
                    "vol_60d": volatility,
                    "sharpe_60d": sharpe,
                    "sortino_downside_deviation_60d": downside,
                    "sortino_60d": sortino,
                }
            )
            frame = self._attach_fundamentals(frame, formation)
            snapshots[formation] = frame.sort_values(
                "ticker", kind="stable", ignore_index=True
            )
        return snapshots

    def _attach_fundamentals(
        self, frame: pd.DataFrame, formation: pd.Timestamp
    ) -> pd.DataFrame:
        sales = self._select_monthly_sales(formation)
        sales_frame = pd.DataFrame.from_dict(sales, orient="index").reindex(
            frame["ticker"].astype(str)
        )
        for column, default in self._empty_sales().items():
            frame[column] = (
                sales_frame[column].to_numpy()
                if column in sales_frame
                else default
            )

        roe = self._select_roe(formation)
        roe_frame = pd.DataFrame.from_dict(roe, orient="index").reindex(
            frame["ticker"].astype(str)
        )
        for column, default in self._empty_roe().items():
            frame[column] = roe_frame[column].to_numpy() if column in roe_frame else default
        return frame

    def _daily_signals(
        self,
        history: pd.DataFrame,
        chip_history: pd.DataFrame,
        days: pd.DatetimeIndex,
        position: int,
    ) -> dict[str, object]:
        last20 = days[max(0, position - 19) : position + 1]
        daily20 = history.reindex(last20)
        traded = pd.to_numeric(daily20.get("traded_value"), errors="coerce")
        turnover = pd.to_numeric(daily20.get("turnover"), errors="coerce")
        adv_count = int(traded.notna().sum())
        turnover_count = int(turnover.notna().sum())

        signals: dict[str, object] = {
            "adv20_observation_count": adv_count,
            "adv20": float(traded.mean()) if adv_count == 20 else math.nan,
            "stock_traded_value_sum20": float(traded.sum())
            if adv_count == 20
            else math.nan,
            "turnover_20d_observation_count": turnover_count,
            "turnover_20d": float(turnover.mean())
            if turnover_count == 20
            else math.nan,
        }

        last80 = days[max(0, position - 79) : position + 1]
        daily80 = history.reindex(last80)
        volume = pd.to_numeric(daily80.get("volume"), errors="coerce")
        denominator = volume.iloc[:60] if len(volume) == 80 else pd.Series(dtype=float)
        numerator = volume.iloc[60:] if len(volume) == 80 else pd.Series(dtype=float)
        denominator_count = int(denominator.notna().sum())
        numerator_count = int(numerator.notna().sum())
        denominator_mean = float(denominator.mean()) if denominator_count == 60 else math.nan
        numerator_mean = float(numerator.mean()) if numerator_count == 20 else math.nan
        signals.update(
            {
                "volume_ratio_numerator_count": numerator_count,
                "volume_ratio_denominator_count": denominator_count,
                "volume_ratio_numerator_mean": numerator_mean,
                "volume_ratio_denominator_mean": denominator_mean,
                "volume_ratio": numerator_mean / denominator_mean
                if denominator_count == 60
                and numerator_count == 20
                and denominator_mean > 0
                else math.nan,
            }
        )

        chip20 = chip_history.reindex(last20)
        chip_fields = chip20.reindex(
            columns=["qfii_examt", "fund_examt", "dlrp_examt"]
        ).apply(pd.to_numeric, errors="coerce")
        complete_chip = chip_fields.notna().all(axis=1)
        chip_count = int(complete_chip.sum())
        signals.update(
            {
                "chip_20d_observation_count": chip_count,
                "chip_20d": float(chip_fields.sum(axis=1).sum())
                if len(last20) == 20 and chip_count == 20
                else math.nan,
            }
        )

        if position >= 252:
            recent_date, old_date = days[position - 21], days[position - 252]
            recent = self._series_value(history, recent_date, "adj_close")
            old = self._series_value(history, old_date, "adj_close")
            momentum = recent / old - 1.0 if recent > 0 and old > 0 else math.nan
        else:
            recent_date = old_date = pd.NaT
            recent = old = math.nan
            momentum = math.nan
        signals.update(
            {
                "momentum_recent_date": recent_date,
                "momentum_old_date": old_date,
                "momentum_recent_adj_close": recent,
                "momentum_old_adj_close": old,
                "momentum_12_1": momentum,
            }
        )

        last61 = days[max(0, position - 60) : position + 1]
        adjusted = pd.to_numeric(
            history.reindex(last61).get("adj_close"), errors="coerce"
        )
        returns = adjusted.div(adjusted.shift(1)).sub(1.0).iloc[1:]
        returns = returns.replace([np.inf, -np.inf], np.nan)
        valid_returns = returns.dropna()
        return_count = int(valid_returns.size)
        sample_std = (
            float(valid_returns.std(ddof=1)) if return_count >= 2 else math.nan
        )
        vol = sample_std * math.sqrt(252) if return_count >= 20 and sample_std > 0 else math.nan
        sharpe = (
            float(valid_returns.mean()) / sample_std * math.sqrt(252)
            if return_count == 60 and sample_std > 0
            else math.nan
        )
        if return_count == 60:
            downside_deviation = math.sqrt(
                float(np.square(np.minimum(valid_returns.to_numpy(), 0.0)).mean())
            )
        else:
            downside_deviation = math.nan
        sortino = (
            float(valid_returns.mean()) / downside_deviation * math.sqrt(252)
            if return_count == 60 and downside_deviation > 0
            else math.nan
        )
        signals.update(
            {
                "return_60d_observation_count": return_count,
                "vol_60d": vol,
                "sharpe_60d": sharpe,
                "sortino_downside_deviation_60d": downside_deviation,
                "sortino_60d": sortino,
            }
        )
        return signals

    def _select_monthly_sales(
        self, formation: pd.Timestamp
    ) -> dict[str, dict[str, object]]:
        if self.monthly_sales.empty:
            return {}
        frame = self.monthly_sales.copy()
        frame["r18"] = pd.to_numeric(frame["r18"], errors="coerce")
        frame = frame[
            frame["source_available_date"].le(formation)
            & frame["source_period_date"].notna()
            & np.isfinite(frame["r18"])
        ].copy()
        frame["r18_period_age_months"] = (
            (formation.year - frame["source_period_date"].dt.year) * 12
            + formation.month
            - frame["source_period_date"].dt.month
        )
        frame = frame[frame["r18_period_age_months"].between(0, 2)]
        if frame.empty:
            return {}
        frame["source_row_id"] = frame.get("source_row_id", "").astype(str)
        frame = frame.sort_values(
            ["ticker", "source_period_date", "source_available_date", "source_row_id"],
            kind="stable",
        ).drop_duplicates("ticker", keep="last")
        return {
            str(row.ticker): {
                "r18": float(row.r18),
                "r18_source_period_date": row.source_period_date,
                "r18_source_available_date": row.source_available_date,
                "r18_period_age_months": int(row.r18_period_age_months),
            }
            for row in frame.itertuples(index=False)
        }

    def _select_roe(self, formation: pd.Timestamp) -> dict[str, dict[str, object]]:
        if self.financial.empty:
            return {}
        frame = self.financial.copy()
        frame["r103"] = pd.to_numeric(frame["r103"], errors="coerce")
        frame = frame[
            frame["no"].eq("TTM")
            & frame["merg"].eq("Y")
            & frame["curr"].eq("NTD")
            & frame["source_available_date"].le(formation)
            & (frame["revision_date"].isna() | frame["revision_date"].le(formation))
            & frame["period_end_date"].notna()
            & np.isfinite(frame["r103"])
            & frame["r103"].gt(0)
        ].copy()
        frame["r103_age_days"] = (formation - frame["period_end_date"]).dt.days
        frame = frame[frame["r103_age_days"].between(0, 180)]
        if frame.empty:
            return {}
        if "revision_date" not in frame:
            frame["revision_date"] = pd.NaT
        frame["source_row_id"] = frame.get("source_row_id", "").astype(str)
        frame = frame.sort_values(
            [
                "ticker",
                "period_end_date",
                "source_available_date",
                "revision_date",
                "source_row_id",
            ],
            kind="stable",
            na_position="first",
        ).drop_duplicates("ticker", keep="last")
        return {
            str(row.ticker): {
                "r103": float(row.r103),
                "r103_period_end_date": row.period_end_date,
                "r103_source_available_date": row.source_available_date,
                "r103_revision_date": row.revision_date,
                "r103_age_days": int(row.r103_age_days),
            }
            for row in frame.itertuples(index=False)
        }

    @staticmethod
    def _normalize_daily(frame: pd.DataFrame | None) -> pd.DataFrame:
        if frame is None:
            raise FeatureContractError("missing daily_price_volume panel")
        missing = sorted(_DAILY_COLUMNS.difference(frame.columns))
        if missing:
            raise FeatureContractError(f"daily_price_volume missing columns: {missing}")
        result = frame.copy()
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        result["ticker"] = result["ticker"].astype(str)
        return result.sort_values(["ticker", "date"], kind="stable", ignore_index=True)

    @staticmethod
    def _normalize_chip(frame: pd.DataFrame | None) -> pd.DataFrame:
        if frame is None:
            raise FeatureContractError("missing daily_chip panel")
        missing = sorted(_CHIP_COLUMNS.difference(frame.columns))
        if missing:
            raise FeatureContractError(f"daily_chip missing columns: {missing}")
        result = frame.copy()
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        result["ticker"] = result["ticker"].astype(str)
        return result.sort_values(["ticker", "date"], kind="stable", ignore_index=True)

    @staticmethod
    def _normalize_dates(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
        if frame.empty:
            return frame
        frame["ticker"] = frame["ticker"].astype(str)
        for column in columns:
            if column in frame:
                frame[column] = pd.to_datetime(frame[column], errors="coerce")
        return frame

    @staticmethod
    def _series_value(frame: pd.DataFrame, date: pd.Timestamp, column: str) -> float:
        if date not in frame.index:
            return math.nan
        value = frame.at[date, column]
        if isinstance(value, pd.Series):
            raise FeatureContractError(f"duplicate ticker/date values for {column} at {date}")
        return PITFeatureEngine._finite_or_nan(value)

    @staticmethod
    def _finite_or_nan(value: object) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return math.nan
        return numeric if math.isfinite(numeric) else math.nan

    @staticmethod
    def _empty_sales() -> dict[str, object]:
        return {
            "r18": math.nan,
            "r18_source_period_date": pd.NaT,
            "r18_source_available_date": pd.NaT,
            "r18_period_age_months": math.nan,
        }

    @staticmethod
    def _empty_roe() -> dict[str, object]:
        return {
            "r103": math.nan,
            "r103_period_end_date": pd.NaT,
            "r103_source_available_date": pd.NaT,
            "r103_revision_date": pd.NaT,
            "r103_age_days": math.nan,
        }
