from __future__ import annotations

import pandas as pd


class CalendarContractError(RuntimeError):
    pass


class TradingCalendar:
    def __init__(self, frame: pd.DataFrame) -> None:
        required = {"date", "market", "is_trading_day"}
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise CalendarContractError(f"trading calendar missing columns: {missing}")

        normalized = frame.loc[:, ["date", "market", "is_trading_day"]].copy()
        normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
        twse = normalized[normalized["market"] == "TWSE"].copy()
        invalid_dates = twse[twse["date"].isna()]
        if not invalid_dates.empty:
            raise CalendarContractError("trading calendar contains invalid TWSE dates")
        duplicates = twse[twse.duplicated("date", keep=False)]["date"]
        if not duplicates.empty:
            values = sorted({value.strftime("%Y-%m-%d") for value in duplicates})
            raise CalendarContractError(f"duplicate TWSE calendar dates: {values}")

        days = twse[twse["is_trading_day"].eq(True)]["date"].sort_values()
        self._days = pd.DatetimeIndex(days.to_list(), name="date")

    @property
    def days(self) -> tuple[pd.Timestamp, ...]:
        return tuple(self._days)

    def trading_days(
        self, start: str | pd.Timestamp, end: str | pd.Timestamp
    ) -> tuple[pd.Timestamp, ...]:
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        return tuple(self._days[(self._days >= start_ts) & (self._days <= end_ts)])

    def month(self, value: str | pd.Timestamp) -> tuple[pd.Timestamp, ...]:
        period = pd.Timestamp(value).to_period("M")
        return tuple(self._days[self._days.to_period("M") == period])

    def month_end(self, value: str | pd.Timestamp) -> pd.Timestamp:
        days = self.month(value)
        if not days:
            raise CalendarContractError(f"month has no TWSE trading days: {value}")
        return days[-1]

    def formation_dates(
        self, start: str | pd.Timestamp, end: str | pd.Timestamp
    ) -> tuple[pd.Timestamp, ...]:
        days = self.trading_days(start, end)
        if not days:
            return ()
        series = pd.Series(days)
        return tuple(series.groupby(series.dt.to_period("M")).max().sort_values())
