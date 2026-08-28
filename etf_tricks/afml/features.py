from __future__ import annotations

import numpy as np
import pandas as pd

from etf_tricks.result import ETFTrickResult

from .config import AFMLConfig, AFMLContractError, config_sha256


_STRUCTURAL_NAMES = (
    "sadf",
    "qadf",
    "qadf_dispersion",
    "cadf",
    "cadf_dispersion",
    "sadf_cadf_z",
    "adf_window_count",
    "adf_valid_window_count",
    "structural_quality_reason",
)


class AFMLFeatureEngine:
    """Build causal, unscaled features for completed ETF Dollar bars."""

    def __init__(self, config: AFMLConfig) -> None:
        self.config = config

    def build(
        self,
        bars: pd.DataFrame,
        memberships: pd.DataFrame,
        ffd: pd.DataFrame,
        structural_etf: pd.DataFrame,
        structural_ix: pd.DataFrame,
        base: ETFTrickResult,
    ) -> pd.DataFrame:
        del memberships  # retained in the public contract for later path features
        if not isinstance(base, ETFTrickResult):
            raise AFMLContractError("base must be an ETFTrickResult")
        _require_unique(bars, ("etf_id", "bar_id"), "bars")
        _require_unique(ffd, ("etf_id", "bar_id"), "ffd")
        _require_unique(structural_etf, ("etf_id", "bar_id"), "structural_etf")
        required_bar = {
            "bar_status",
            "bar_end_date",
            "close_nav",
            "log_return",
            "bar_amount",
            "feature_available_at",
        }
        missing_bar = sorted(required_bar.difference(bars.columns))
        if missing_bar:
            raise AFMLContractError(f"bars missing required columns: {missing_bar}")

        result = bars[bars["bar_status"].eq("FINALIZED")].copy()
        result["bar_end_date"] = pd.to_datetime(result["bar_end_date"], errors="coerce")
        result["feature_available_at"] = pd.to_datetime(
            result["feature_available_at"], errors="coerce"
        )
        if result[["bar_end_date", "feature_available_at"]].isna().any().any():
            raise AFMLContractError("bar dates and availability must be valid")
        result = result.sort_values(["etf_id", "bar_id"], kind="stable")

        result = self._join_ffd(result, ffd)
        result = self._join_etf_structural(result, structural_etf)
        result = self._add_group_features(result)
        result = self._join_portfolio_state(result, base)
        result = self._join_market_features(result, structural_ix)
        result = self._add_interval_market_features(result)

        result["feature_config_hash"] = config_sha256(self.config.features)
        result["afml_config_hash"] = config_sha256(self.config)
        result["ffd_missing"] = result["ffd_level"].isna()
        result["structural_etf_missing"] = result["etf_sadf"].isna()
        result["ix_missing"] = result["ix_sadf"].isna()
        result["portfolio_state_missing"] = result["cash_weight"].isna()
        if result.duplicated(["etf_id", "bar_id"]).any():
            raise AFMLContractError("feature output has duplicate etf_id-bar_id keys")
        return result.sort_values(["etf_id", "bar_id"], kind="stable").reset_index(
            drop=True
        )

    @staticmethod
    def _join_ffd(result: pd.DataFrame, ffd: pd.DataFrame) -> pd.DataFrame:
        rename = {
            "calibration_version": "ffd_calibration_version",
            "config_version": "ffd_config_version",
            "feature_available_at": "ffd_feature_available_at",
        }
        columns = [
            column
            for column in (
                "etf_id",
                "bar_id",
                "ffd_level",
                "selected_d",
                "ffd_width",
                "calibration_version",
                "config_version",
                "feature_available_at",
            )
            if column in ffd.columns
        ]
        if "ffd_level" not in columns:
            raise AFMLContractError("ffd missing required column: ffd_level")
        return result.merge(
            ffd[columns].rename(columns=rename),
            on=["etf_id", "bar_id"],
            how="left",
            validate="one_to_one",
        )

    @staticmethod
    def _join_etf_structural(
        result: pd.DataFrame, structural: pd.DataFrame
    ) -> pd.DataFrame:
        columns = ["etf_id", "bar_id", "feature_available_at"] + [
            name for name in _STRUCTURAL_NAMES if name in structural.columns
        ]
        missing = sorted({"sadf", "qadf", "cadf"}.difference(columns))
        if missing:
            raise AFMLContractError(
                f"structural_etf missing required statistics: {missing}"
            )
        rename = {
            name: f"etf_{name}"
            for name in columns
            if name not in {"etf_id", "bar_id", "feature_available_at"}
        }
        rename["feature_available_at"] = "etf_structural_available_at"
        merged = result.merge(
            structural[columns].rename(columns=rename),
            on=["etf_id", "bar_id"],
            how="left",
            validate="one_to_one",
        )
        for source_column in (
            "ffd_feature_available_at",
            "etf_structural_available_at",
        ):
            if source_column in merged.columns:
                source = pd.to_datetime(merged[source_column], errors="coerce")
                merged["feature_available_at"] = _rowwise_timestamp_max(
                    merged["feature_available_at"], source
                )
        return merged

    def _add_group_features(self, frame: pd.DataFrame) -> pd.DataFrame:
        pieces = [
            self._add_one_group(group.copy())
            for _, group in frame.groupby("etf_id", sort=False, dropna=False)
        ]
        return pd.concat(pieces, ignore_index=True) if pieces else frame.copy()

    def _add_one_group(self, group: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config.features
        group = group.sort_values("bar_id", kind="stable").reset_index(drop=True)
        ffd = pd.to_numeric(group["ffd_level"], errors="coerce")
        ma = ffd.rolling(cfg.ffd_ma_window, min_periods=cfg.ffd_ma_window).mean()
        level_std = ffd.rolling(
            cfg.ffd_ma_window, min_periods=cfg.ffd_ma_window
        ).std(ddof=1)
        group[f"ffd_ma_{cfg.ffd_ma_window}"] = ma
        group[f"ffd_ma_distance_{cfg.ffd_ma_window}"] = ffd - ma
        group[f"ffd_ma_distance_z_{cfg.ffd_ma_window}"] = (ffd - ma) / level_std
        for window in cfg.ffd_vol_windows:
            group[f"ffd_level_std_{window}"] = ffd.rolling(
                window, min_periods=window
            ).std(ddof=1)
            group[f"ffd_change_vol_{window}"] = ffd.diff().rolling(
                window, min_periods=window
            ).std(ddof=1)
        group[f"ffd_level_skew_{cfg.shape_window}"] = ffd.rolling(
            cfg.shape_window, min_periods=cfg.min_shape_obs
        ).skew()
        group[f"ffd_level_excess_kurtosis_{cfg.shape_window}"] = ffd.rolling(
            cfg.shape_window, min_periods=cfg.min_shape_obs
        ).kurt()

        returns = pd.to_numeric(group["log_return"], errors="coerce")
        group[f"log_return_vol_{cfg.shape_window}"] = returns.rolling(
            cfg.shape_window, min_periods=cfg.min_shape_obs
        ).std(ddof=1)
        downside_square = returns.clip(upper=0).pow(2)
        group[f"downside_vol_{cfg.shape_window}"] = np.sqrt(
            downside_square.rolling(
                cfg.shape_window, min_periods=cfg.min_shape_obs
            ).mean()
        )
        log_close = np.log(pd.to_numeric(group["close_nav"], errors="coerce"))
        delta = log_close.diff().abs()
        denominator = delta.rolling(
            cfg.efficiency_window, min_periods=cfg.efficiency_window
        ).sum()
        group[f"efficiency_ratio_{cfg.efficiency_window}"] = (
            (log_close - log_close.shift(cfg.efficiency_window)).abs() / denominator
        )
        rolling_peak = log_close.rolling(
            cfg.shape_window, min_periods=1
        ).max()
        group[f"rolling_drawdown_{cfg.shape_window}"] = log_close - rolling_peak
        high = pd.to_numeric(group.get("close_path_high_nav"), errors="coerce")
        low = pd.to_numeric(group.get("close_path_low_nav"), errors="coerce")
        group["close_path_range"] = np.log(high / low)

        duration = pd.to_numeric(group.get("trading_day_count"), errors="coerce")
        prior_duration = duration.shift(1).rolling(
            cfg.amount_window, min_periods=cfg.amount_window
        ).median()
        group[f"duration_surprise_{cfg.amount_window}"] = duration / prior_duration

        amount = pd.to_numeric(group["bar_amount"], errors="coerce")
        group["bar_amount"] = amount
        group["log1p_bar_amount"] = np.log1p(amount)
        prior_amount = amount.shift(1)
        prior_mean = prior_amount.rolling(
            cfg.amount_window, min_periods=cfg.amount_window
        ).mean()
        group[f"amount_ratio_{cfg.amount_window}"] = amount / prior_mean
        ewm_mean = prior_amount.ewm(
            span=cfg.amount_window,
            adjust=False,
            min_periods=cfg.amount_window,
        ).mean()
        ewm_std = prior_amount.ewm(
            span=cfg.amount_window,
            adjust=False,
            min_periods=cfg.amount_window,
        ).std(bias=False)
        group[f"amount_ewma_z_{cfg.amount_window}"] = (amount - ewm_mean) / ewm_std

        amihud = returns.abs() / amount
        group["amihud_illiquidity"] = amihud
        group[f"amihud_mean_{cfg.amount_window}"] = amihud.rolling(
            cfg.amount_window, min_periods=cfg.amount_window
        ).mean()
        slope, t_stat = _rolling_linear_trend(amihud, cfg.amount_window)
        group[f"amihud_trend_{cfg.amount_window}"] = slope
        group[f"amihud_trend_tstat_{cfg.amount_window}"] = t_stat

        lag_cov = returns.rolling(
            cfg.amount_window, min_periods=cfg.amount_window
        ).cov(returns.shift(1))
        valid_roll = lag_cov.lt(0)
        roll_spread = pd.Series(np.nan, index=group.index, dtype=float)
        roll_spread.loc[valid_roll] = 2.0 * np.sqrt(-lag_cov.loc[valid_roll])
        group[f"roll_spread_{cfg.amount_window}"] = roll_spread
        group[f"roll_spread_reason_{cfg.amount_window}"] = np.where(
            lag_cov.isna(),
            "INSUFFICIENT_OBSERVATIONS",
            np.where(valid_roll, None, "NONNEGATIVE_SERIAL_COVARIANCE"),
        )
        return group

    @staticmethod
    def _join_portfolio_state(
        result: pd.DataFrame, base: ETFTrickResult
    ) -> pd.DataFrame:
        daily_columns = [
            "date",
            "etf_id",
            "cash_weight",
            "invested_weight",
            "holdings_count",
            "target_completion_ratio",
        ]
        missing_daily = sorted(set(daily_columns).difference(base.daily_etf.columns))
        if missing_daily:
            raise AFMLContractError(
                f"base.daily_etf missing portfolio state columns: {missing_daily}"
            )
        daily = base.daily_etf[daily_columns].copy()
        daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
        _require_unique(daily, ("etf_id", "date"), "base.daily_etf")
        merged = result.merge(
            daily.rename(columns={"date": "bar_end_date"}),
            on=["etf_id", "bar_end_date"],
            how="left",
            validate="many_to_one",
        )

        if base.daily_holdings.empty:
            merged["portfolio_hhi"] = np.nan
            merged["portfolio_hhi_reason"] = "DAILY_HOLDINGS_UNAVAILABLE"
            merged["realized_constituent_replacement_ratio"] = np.nan
            merged["realized_weight_turnover"] = np.nan
            merged["realized_portfolio_change_reason"] = "DAILY_HOLDINGS_UNAVAILABLE"
            return merged
        required_holding = {"date", "etf_id", "ticker", "actual_weight"}
        missing = sorted(required_holding.difference(base.daily_holdings.columns))
        if missing:
            raise AFMLContractError(f"base.daily_holdings missing columns: {missing}")
        holdings = base.daily_holdings[list(required_holding)].copy()
        holdings["date"] = pd.to_datetime(holdings["date"], errors="coerce")
        if holdings.duplicated(["date", "etf_id", "ticker"]).any():
            raise AFMLContractError("base.daily_holdings has duplicate keys")
        holdings["actual_weight"] = pd.to_numeric(
            holdings["actual_weight"], errors="coerce"
        )
        hhi = (
            holdings.assign(weight_square=lambda x: x["actual_weight"].pow(2))
            .groupby(["date", "etf_id"], as_index=False)["weight_square"]
            .sum()
            .rename(columns={"date": "bar_end_date", "weight_square": "portfolio_hhi"})
        )
        merged = merged.merge(
            hhi, on=["etf_id", "bar_end_date"], how="left", validate="many_to_one"
        )
        merged["portfolio_hhi_reason"] = np.where(
            merged["portfolio_hhi"].isna(), "NO_HOLDINGS_AT_BAR_END", None
        )
        changes = _monthly_realized_changes(holdings)
        merged = _backward_month_join(merged, changes)
        return merged

    def _join_market_features(
        self, result: pd.DataFrame, structural_ix: pd.DataFrame
    ) -> pd.DataFrame:
        required = {"feature_available_at", "structural_source_value", "sadf"}
        missing = sorted(required.difference(structural_ix.columns))
        if missing:
            raise AFMLContractError(f"structural_ix missing columns: {missing}")
        ix = structural_ix.copy()
        ix["feature_available_at"] = pd.to_datetime(
            ix["feature_available_at"], errors="coerce"
        )
        if ix["feature_available_at"].isna().any():
            raise AFMLContractError("structural_ix availability must be valid")
        if ix.duplicated("feature_available_at").any():
            raise AFMLContractError("structural_ix has duplicate availability keys")
        ix = ix.sort_values("feature_available_at", kind="stable").reset_index(drop=True)
        ix_log = pd.to_numeric(ix["structural_source_value"], errors="coerce")
        ix["ix_log_close"] = ix_log
        ix["ix_log_return"] = ix_log.diff()
        for window in self.config.features.market_vol_windows:
            ix[f"ix_log_return_vol_{window}"] = ix["ix_log_return"].rolling(
                window, min_periods=window
            ).std(ddof=1)
        ix["ix_drawdown"] = ix_log - ix_log.cummax()
        ix["ix_feature_available_at"] = ix["feature_available_at"]
        ix["ix_observation_date"] = (
            pd.to_datetime(ix["date"], errors="coerce")
            if "date" in ix.columns
            else ix["feature_available_at"].dt.tz_localize(None).dt.normalize()
        )
        for name in _STRUCTURAL_NAMES:
            if name in ix.columns:
                ix[f"ix_{name}"] = ix[name]
        keep = [column for column in ix.columns if column.startswith("ix_")]

        left = result.copy()
        left["__feature_order"] = np.arange(len(left), dtype=np.int64)
        left = left.sort_values("feature_available_at", kind="stable")
        joined = pd.merge_asof(
            left,
            ix[["feature_available_at", *keep]],
            on="feature_available_at",
            direction="backward",
            allow_exact_matches=True,
        )
        joined = joined.sort_values("__feature_order", kind="stable").drop(
            columns="__feature_order"
        )
        joined["ix_staleness_trading_days"] = _market_staleness_sessions(
            joined["bar_end_date"], joined["ix_observation_date"], ix["ix_observation_date"]
        )
        no_prior = joined["ix_feature_available_at"].isna()
        stale = joined["ix_staleness_trading_days"].gt(
            self.config.pit.max_environment_staleness_trading_days
        )
        joined["ix_alignment_reason"] = np.where(
            no_prior,
            "NO_PRIOR_MARKET_OBSERVATION",
            np.where(stale, "STALE_MARKET_OBSERVATION", None),
        )
        if stale.any():
            protected = {
                "ix_feature_available_at",
                "ix_observation_date",
                "ix_staleness_trading_days",
                "ix_alignment_reason",
            }
            value_columns = [
                column
                for column in joined.columns
                if column.startswith("ix_") and column not in protected
            ]
            joined.loc[stale, value_columns] = np.nan
        return joined

    def _add_interval_market_features(self, frame: pd.DataFrame) -> pd.DataFrame:
        pieces: list[pd.DataFrame] = []
        window = self.config.features.beta_window
        for _, group in frame.groupby("etf_id", sort=False, dropna=False):
            group = group.sort_values("bar_id", kind="stable").copy()
            group["ix_interval_log_return"] = group["ix_log_close"].diff()
            etf_returns = pd.to_numeric(group["log_return"], errors="coerce")
            ix_returns = pd.to_numeric(
                group["ix_interval_log_return"], errors="coerce"
            )
            covariance = etf_returns.rolling(window, min_periods=window).cov(ix_returns)
            variance = ix_returns.rolling(window, min_periods=window).var(ddof=1)
            group[f"etf_ix_beta_{window}"] = covariance / variance
            group[f"etf_ix_correlation_{window}"] = etf_returns.rolling(
                window, min_periods=window
            ).corr(ix_returns)
            pieces.append(group)
        return pd.concat(pieces, ignore_index=True) if pieces else frame.copy()


def _require_unique(frame: pd.DataFrame, key: tuple[str, ...], name: str) -> None:
    missing = sorted(set(key).difference(frame.columns))
    if missing:
        raise AFMLContractError(f"{name} missing key columns: {missing}")
    if frame.duplicated(list(key)).any():
        raise AFMLContractError(f"{name} has duplicate {key} keys")


def _rowwise_timestamp_max(left: pd.Series, right: pd.Series) -> pd.Series:
    left_values = pd.to_datetime(left, errors="coerce")
    right_values = pd.to_datetime(right, errors="coerce")
    return left_values.where(right_values.isna() | left_values.ge(right_values), right_values)


def _rolling_linear_trend(
    values: pd.Series, window: int
) -> tuple[pd.Series, pd.Series]:
    position = pd.Series(np.arange(len(values), dtype=float), index=values.index)
    count = values.rolling(window, min_periods=window).count()
    sum_y = values.rolling(window, min_periods=window).sum()
    sum_y2 = values.pow(2).rolling(window, min_periods=window).sum()
    sum_global_xy = (values * position).rolling(window, min_periods=window).sum()
    start = position - window + 1
    sum_xy = sum_global_xy - start * sum_y
    sum_x = window * (window - 1) / 2.0
    sum_x2 = window * (window - 1) * (2 * window - 1) / 6.0
    ss_x = sum_x2 - sum_x**2 / window
    slope = (sum_xy - sum_x * sum_y / window) / ss_x
    intercept = (sum_y - slope * sum_x) / window
    residual_ss = sum_y2 - intercept * sum_y - slope * sum_xy
    sigma2 = residual_ss.clip(lower=0) / (window - 2)
    standard_error = np.sqrt(sigma2 / ss_x)
    t_stat = slope / standard_error
    invalid = count.ne(window) | standard_error.le(0)
    return slope.mask(invalid), t_stat.mask(invalid)


def _monthly_realized_changes(holdings: pd.DataFrame) -> pd.DataFrame:
    working = holdings.copy()
    working["month"] = working["date"].dt.to_period("M")
    last_dates = working.groupby(["etf_id", "month"])["date"].transform("max")
    month_end = working[working["date"].eq(last_dates)]
    rows: list[dict[str, object]] = []
    for etf_id, group in month_end.groupby("etf_id", sort=False):
        months = sorted(group["month"].unique())
        previous: dict[str, float] | None = None
        for month in months:
            current_rows = group[group["month"].eq(month)]
            current = dict(
                zip(
                    current_rows["ticker"].astype(str),
                    current_rows["actual_weight"].astype(float),
                    strict=True,
                )
            )
            replacement = np.nan
            turnover = np.nan
            if previous is not None:
                prior_names = set(previous)
                replacement = (
                    1.0 - len(prior_names.intersection(current)) / len(prior_names)
                    if prior_names
                    else np.nan
                )
                names = prior_names.union(current)
                turnover = 0.5 * sum(
                    abs(current.get(name, 0.0) - previous.get(name, 0.0))
                    for name in names
                )
            rows.append(
                {
                    "etf_id": etf_id,
                    "completed_month": month,
                    "realized_constituent_replacement_ratio": replacement,
                    "realized_weight_turnover": turnover,
                }
            )
            previous = current
    return pd.DataFrame(rows)


def _backward_month_join(result: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    output = result.copy()
    output["realized_constituent_replacement_ratio"] = np.nan
    output["realized_weight_turnover"] = np.nan
    output["realized_portfolio_change_reason"] = "NO_PRIOR_COMPLETED_MONTH"
    if changes.empty:
        return output
    for index, row in output.iterrows():
        current_month = pd.Timestamp(row["bar_end_date"]).to_period("M")
        eligible = changes[
            changes["etf_id"].eq(row["etf_id"])
            & changes["completed_month"].lt(current_month)
        ]
        if eligible.empty:
            continue
        selected = eligible.sort_values("completed_month").iloc[-1]
        output.at[index, "realized_constituent_replacement_ratio"] = selected[
            "realized_constituent_replacement_ratio"
        ]
        output.at[index, "realized_weight_turnover"] = selected[
            "realized_weight_turnover"
        ]
        output.at[index, "realized_portfolio_change_reason"] = (
            None
            if pd.notna(selected["realized_weight_turnover"])
            else "NO_PREVIOUS_MONTH_FOR_COMPARISON"
        )
    return output


def _market_staleness_sessions(
    bar_dates: pd.Series, matched_dates: pd.Series, market_dates: pd.Series
) -> pd.Series:
    calendar = pd.DatetimeIndex(pd.to_datetime(market_dates).dropna().unique()).sort_values()
    result = np.full(len(bar_dates), np.nan, dtype=float)
    for position, (bar_date, matched_date) in enumerate(zip(bar_dates, matched_dates)):
        if pd.isna(bar_date) or pd.isna(matched_date):
            continue
        expected = calendar.searchsorted(pd.Timestamp(bar_date), side="right") - 1
        matched = calendar.searchsorted(pd.Timestamp(matched_date), side="right") - 1
        if expected >= 0 and matched >= 0:
            result[position] = max(int(expected - matched), 0)
    return pd.Series(result, index=bar_dates.index)
