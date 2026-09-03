from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from .config import AFMLContractError, StructuralConfig, config_sha256


_STATISTIC_COLUMNS = (
    "sadf",
    "qadf",
    "qadf_dispersion",
    "cadf",
    "cadf_dispersion",
    "sadf_cadf_z",
)


def adf_start_vector(
    log_prices: Iterable[float],
    end: int,
    min_sample_length: int,
    lags: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return right-tail ADF t-statistics for every governed start.

    ``end`` is inclusive. Each regression is
    ``delta(y_t) ~ 1 + y_(t-1) + lagged delta(y)``. Cross products are
    accumulated once and sliced as suffixes instead of rebuilding OLS inputs.
    """

    values = np.asarray(tuple(log_prices), dtype=float)
    if values.ndim != 1:
        raise AFMLContractError("log_prices must be one-dimensional")
    if not np.isfinite(values).all():
        raise AFMLContractError("log_prices must contain only finite values")
    if not isinstance(end, (int, np.integer)) or end < 0 or end >= len(values):
        raise AFMLContractError("end must be a valid inclusive position")
    if lags < 0:
        raise AFMLContractError("lags must be non-negative")
    if min_sample_length <= lags + 2:
        raise AFMLContractError(
            "min_sample_length must exceed lags + 2 regression observations"
        )
    if end + 1 < min_sample_length:
        return np.array([], dtype=np.int64), np.array([], dtype=float)

    endpoint = values[: end + 1]
    differences = np.diff(endpoint)
    row_times = np.arange(lags + 1, end + 1, dtype=np.int64)
    dependent = differences[row_times - 1]
    columns = [np.ones(len(row_times), dtype=float), endpoint[row_times - 1]]
    for lag in range(1, lags + 1):
        columns.append(differences[row_times - lag - 1])
    design = np.column_stack(columns)

    xx_rows = np.einsum("ni,nj->nij", design, design)
    xy_rows = design * dependent[:, None]
    yy_rows = dependent * dependent
    suffix_xx = np.cumsum(xx_rows[::-1], axis=0)[::-1]
    suffix_xy = np.cumsum(xy_rows[::-1], axis=0)[::-1]
    suffix_yy = np.cumsum(yy_rows[::-1], axis=0)[::-1]

    starts = np.arange(end - min_sample_length + 2, dtype=np.int64)
    xx = suffix_xx[starts]
    xy = suffix_xy[starts]
    yy = suffix_yy[starts]
    # In normal ADF windows the design is full rank.  A batched solve avoids
    # the SVD performed by ``pinv`` for every governed start.  Preserve the
    # pseudo-inverse path for any degenerate batch, so numerical semantics do
    # not silently change for rank-deficient histories.
    try:
        coefficients = np.linalg.solve(xx, xy[..., None])[..., 0]
        beta_basis = np.zeros((len(xx), xx.shape[1]), dtype=float)
        beta_basis[:, 1] = 1.0
        beta_inverse_diagonal = np.linalg.solve(xx, beta_basis[..., None])[..., 1, 0]
        ranks = np.full(len(xx), xx.shape[1], dtype=np.int64)
    except np.linalg.LinAlgError:
        inverses = np.linalg.pinv(xx, hermitian=True)
        coefficients = np.einsum("nij,nj->ni", inverses, xy)
        beta_inverse_diagonal = inverses[:, 1, 1]
        ranks = np.linalg.matrix_rank(xx)
    residual_ss = (
        yy
        - 2.0 * np.einsum("ni,ni->n", coefficients, xy)
        + np.einsum("ni,nij,nj->n", coefficients, xx, coefficients)
    )
    residual_ss = np.maximum(residual_ss, 0.0)
    observation_counts = len(row_times) - starts
    degrees_freedom = observation_counts - ranks
    variance = np.divide(
        residual_ss,
        degrees_freedom,
        out=np.full_like(residual_ss, np.nan),
        where=degrees_freedom > 0,
    )
    beta_variance = variance * beta_inverse_diagonal
    beta_standard_error = np.sqrt(np.maximum(beta_variance, 0.0))
    statistics = np.divide(
        coefficients[:, 1],
        beta_standard_error,
        out=np.full(len(starts), np.nan, dtype=float),
        where=beta_standard_error > 0,
    )
    return starts, statistics


def structural_statistics(
    adf_values: Iterable[float],
    q: float,
    v: float,
    quantile_method: str,
) -> dict[str, float | int | str | None]:
    """Summarize one shared ADF vector as SADF, QADF and Conditional ADF."""

    if not 0 < q < 1:
        raise AFMLContractError("q must satisfy 0 < q < 1")
    if not 0 < v <= min(q, 1 - q):
        raise AFMLContractError("v must satisfy 0 < v <= min(q, 1-q)")
    values = np.asarray(tuple(adf_values), dtype=float)
    if values.ndim != 1:
        raise AFMLContractError("adf_values must be one-dimensional")
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {
            **{name: np.nan for name in _STATISTIC_COLUMNS},
            "adf_valid_window_count": 0,
            "structural_quality_reason": "NO_VALID_ADF_WINDOWS",
        }

    qadf = float(np.quantile(finite, q, method=quantile_method))
    lower = float(np.quantile(finite, q - v, method=quantile_method))
    upper = float(np.quantile(finite, q + v, method=quantile_method))
    tail = finite[finite >= qadf]
    cadf = float(tail.mean())
    cadf_dispersion = float(tail.std(ddof=0))
    sadf = float(finite.max())
    if not np.isfinite(cadf_dispersion) or cadf_dispersion == 0.0:
        z_score = np.nan
        reason: str | None = "ZERO_CADF_DISPERSION"
    else:
        z_score = float((sadf - cadf) / cadf_dispersion)
        reason = None
    return {
        "sadf": sadf,
        "qadf": qadf,
        "qadf_dispersion": upper - lower,
        "cadf": cadf,
        "cadf_dispersion": cadf_dispersion,
        "sadf_cadf_z": z_score,
        "adf_valid_window_count": int(finite.size),
        "structural_quality_reason": reason,
    }


class StructuralFeatureEngine:
    def __init__(self, config: StructuralConfig) -> None:
        self.config = config

    def transform(
        self,
        frame: pd.DataFrame,
        value_column: str,
        available_at_column: str,
    ) -> pd.DataFrame:
        required = {value_column, available_at_column}
        missing = required.difference(frame.columns)
        if missing:
            raise AFMLContractError(
                f"structural input missing required columns: {sorted(missing)}"
            )
        if frame.empty:
            return self._empty_result(frame)

        available = pd.to_datetime(frame[available_at_column], errors="coerce")
        if available.isna().any():
            raise AFMLContractError(f"{available_at_column} must contain valid timestamps")
        values = pd.to_numeric(frame[value_column], errors="coerce")
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise AFMLContractError(f"{value_column} must contain only finite values")

        working = frame.copy()
        working["__structural_position"] = np.arange(len(working), dtype=np.int64)
        if "etf_id" in working.columns:
            groups = working.groupby("etf_id", sort=False, dropna=False)
        else:
            groups = [(None, working)]
        pieces: list[pd.DataFrame] = []
        for _, group in groups:
            ordered = group.sort_values(
                [available_at_column, "__structural_position"], kind="stable"
            )
            pieces.append(
                self._transform_group(ordered, value_column, available_at_column)
            )
        result = pd.concat(pieces, ignore_index=True)
        return (
            result.sort_values("__structural_position", kind="stable")
            .drop(columns="__structural_position")
            .reset_index(drop=True)
        )

    def _transform_group(
        self,
        group: pd.DataFrame,
        value_column: str,
        available_at_column: str,
    ) -> pd.DataFrame:
        values = group[value_column].to_numpy(dtype=float)
        availability = pd.to_datetime(group[available_at_column]).reset_index(drop=True)
        identity_columns = [
            column
            for column in ("etf_id", "bar_id", "bar_end_date", "date")
            if column in group.columns
        ]
        result = group[identity_columns + ["__structural_position"]].reset_index(
            drop=True
        )
        result["structural_source_value"] = values
        result["feature_available_at"] = availability
        rows: list[dict[str, object]] = []
        structural_hash = config_sha256(self.config)
        for end in range(len(group)):
            observation_count = end + 1
            base: dict[str, object] = {
                **{name: np.nan for name in _STATISTIC_COLUMNS},
                "adf_window_count": 0,
                "adf_valid_window_count": 0,
                "adf_min_start": np.nan,
                "adf_max_start": np.nan,
                "adf_maximizing_start": np.nan,
                "source_observation_count": observation_count,
                "source_start_available_at": availability.iloc[0],
                "source_end_available_at": availability.iloc[end],
                "structural_q": self.config.q,
                "structural_v": self.config.v,
                "structural_quantile_method": self.config.quantile_method,
                "structural_lags": self.config.lags,
                "structural_min_sample_length": self.config.min_sample_length,
                "source_value_column": value_column,
                "structural_config_hash": structural_hash,
                "structural_quality_reason": "INSUFFICIENT_OBSERVATIONS",
            }
            if observation_count >= self.config.min_sample_length:
                starts, statistics = adf_start_vector(
                    values,
                    end=end,
                    min_sample_length=self.config.min_sample_length,
                    lags=self.config.lags,
                )
                base.update(
                    structural_statistics(
                        statistics,
                        q=self.config.q,
                        v=self.config.v,
                        quantile_method=self.config.quantile_method,
                    )
                )
                base["adf_window_count"] = int(len(starts))
                if len(starts):
                    base["adf_min_start"] = int(starts.min())
                    base["adf_max_start"] = int(starts.max())
                    finite_positions = np.flatnonzero(np.isfinite(statistics))
                    if finite_positions.size:
                        maximum = finite_positions[
                            np.argmax(statistics[finite_positions])
                        ]
                        base["adf_maximizing_start"] = int(starts[maximum])
            rows.append(base)
        return pd.concat([result, pd.DataFrame(rows)], axis=1)

    @staticmethod
    def _empty_result(frame: pd.DataFrame) -> pd.DataFrame:
        identity_columns = [
            column
            for column in ("etf_id", "bar_id", "bar_end_date", "date")
            if column in frame.columns
        ]
        columns = identity_columns + [
            "structural_source_value",
            "feature_available_at",
            *_STATISTIC_COLUMNS,
            "adf_window_count",
            "adf_valid_window_count",
            "adf_min_start",
            "adf_max_start",
            "adf_maximizing_start",
            "source_observation_count",
            "source_start_available_at",
            "source_end_available_at",
            "structural_q",
            "structural_v",
            "structural_quantile_method",
            "structural_lags",
            "structural_min_sample_length",
            "source_value_column",
            "structural_config_hash",
            "structural_quality_reason",
        ]
        return pd.DataFrame(columns=columns)
