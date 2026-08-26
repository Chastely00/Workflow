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
