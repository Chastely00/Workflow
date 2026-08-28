from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
import hashlib
import json
from typing import Literal, TypeAlias


AFMLRunMode: TypeAlias = Literal["train", "walk_forward", "research_full_history"]
SUPPORTED_AFML_RUN_MODES: tuple[AFMLRunMode, ...] = (
    "train",
    "walk_forward",
    "research_full_history",
)


class AFMLContractError(ValueError):
    """Raised when an AFML data or configuration contract is violated."""


class AFMLScopeError(AFMLContractError):
    """Raised when a requested AFML research scope is not authorized."""


def _require_positive(name: str, value: int | float) -> None:
    if value <= 0:
        raise AFMLContractError(f"{name} must be positive")


@dataclass(frozen=True)
class DollarBarConfig:
    threshold_mode: Literal["lagged_market_fraction", "fixed_nominal"] = (
        "lagged_market_fraction"
    )
    market_amount_lookback_days: int = 60
    min_market_amount_observations: int = 20
    market_fraction: str | float = "auto_train_calibrated"
    candidate_quantile_min: float = 0.01
    candidate_quantile_max: float = 0.99
    candidate_quantile_count: int = 99
    candidate_quantile_method: Literal["linear"] = "linear"
    min_completed_bars: int = 120
    max_bar_duration_trading_days: int = 60
    max_bars_per_day: int = 1
    emit_incomplete_terminal_bar: bool = False
    fixed_nominal_threshold: float | None = None
    quality_policy: Literal["fail", "allow_flagged"] = "fail"

    def __post_init__(self) -> None:
        for name in (
            "market_amount_lookback_days",
            "min_market_amount_observations",
            "candidate_quantile_count",
            "min_completed_bars",
            "max_bar_duration_trading_days",
        ):
            _require_positive(name, getattr(self, name))
        if self.min_market_amount_observations > self.market_amount_lookback_days:
            raise AFMLContractError(
                "min_market_amount_observations cannot exceed "
                "market_amount_lookback_days"
            )
        if not 0 < self.candidate_quantile_min < self.candidate_quantile_max < 1:
            raise AFMLContractError(
                "candidate quantiles must satisfy 0 < min < max < 1"
            )
        if self.max_bars_per_day != 1:
            raise AFMLContractError("max_bars_per_day must equal 1 for daily inputs")
        if self.emit_incomplete_terminal_bar:
            raise AFMLContractError("emit_incomplete_terminal_bar must remain False")
        if self.threshold_mode == "fixed_nominal":
            if self.fixed_nominal_threshold is None or self.fixed_nominal_threshold <= 0:
                raise AFMLContractError(
                    "fixed_nominal_threshold must be positive in fixed_nominal mode"
                )
        elif self.fixed_nominal_threshold is not None:
            raise AFMLContractError(
                "fixed_nominal_threshold is only valid in fixed_nominal mode"
            )
        if isinstance(self.market_fraction, (int, float)):
            if not 0 < float(self.market_fraction) < 1:
                raise AFMLContractError("numeric market_fraction must satisfy 0 < q < 1")
        elif self.market_fraction != "auto_train_calibrated":
            raise AFMLContractError(
                "market_fraction must be auto_train_calibrated or a numeric fraction"
            )


@dataclass(frozen=True)
class FFDConfig:
    input: Literal["log_close_nav"] = "log_close_nav"
    d_search_policy: Literal["autonomous_governed"] = "autonomous_governed"
    d_initial_min: float = 0.0
    d_initial_max: float = 1.0
    d_first_escalation_max: float = 2.0
    d_expansion_span: float = 1.0
    autonomous_max_d: float = 5.0
    coarse_step: float = 0.05
    refine_step: float = 0.01
    weight_tolerance: float = 1e-5
    alpha: float = 0.05
    regression: Literal["c"] = "c"
    maxlag: int = 1
    autolag: None = None
    min_adf_observations: int = 120
    strict_dual_gate: bool = False

    def __post_init__(self) -> None:
        for name in (
            "d_expansion_span",
            "coarse_step",
            "refine_step",
            "weight_tolerance",
            "min_adf_observations",
        ):
            _require_positive(name, getattr(self, name))
        if self.d_initial_min < 0 or self.d_initial_min >= self.d_initial_max:
            raise AFMLContractError("FFD initial d bounds must be ordered and non-negative")
        if self.d_first_escalation_max <= self.d_initial_max:
            raise AFMLContractError(
                "d_first_escalation_max must exceed d_initial_max"
            )
        if self.autonomous_max_d < self.d_first_escalation_max:
            raise AFMLContractError(
                "autonomous_max_d must cover the first escalation interval"
            )
        if self.refine_step > self.coarse_step:
            raise AFMLContractError("refine_step cannot exceed coarse_step")
        if not 0 < self.weight_tolerance < 1:
            raise AFMLContractError("weight_tolerance must satisfy 0 < tolerance < 1")
        if not 0 < self.alpha < 1:
            raise AFMLContractError("alpha must satisfy 0 < alpha < 1")
        if self.maxlag < 0:
            raise AFMLContractError("maxlag must be non-negative")


@dataclass(frozen=True)
class StructuralConfig:
    min_sample_length: int = 60
    lags: int = 1
    regression: Literal["c"] = "c"
    q: float = 0.95
    v: float = 0.025
    quantile_method: Literal["linear"] = "linear"
    conditional_std_ddof: int = 0

    def __post_init__(self) -> None:
        _require_positive("min_sample_length", self.min_sample_length)
        if self.lags < 0:
            raise AFMLContractError("lags must be non-negative")
        if not 0 < self.q < 1:
            raise AFMLContractError("structural q must satisfy 0 < q < 1")
        if not 0 < self.v <= min(self.q, 1 - self.q):
            raise AFMLContractError(
                "structural v must satisfy 0 < v <= min(q, 1-q)"
            )
        if self.conditional_std_ddof != 0:
            raise AFMLContractError("conditional_std_ddof must equal 0")


@dataclass(frozen=True)
class FeatureConfig:
    ffd_ma_window: int = 20
    ffd_vol_windows: tuple[int, ...] = (14, 60)
    shape_window: int = 60
    min_shape_obs: int = 30
    amount_window: int = 20
    efficiency_window: int = 20
    market_vol_windows: tuple[int, ...] = (20, 60)
    beta_window: int = 60

    def __post_init__(self) -> None:
        for name in (
            "ffd_ma_window",
            "shape_window",
            "min_shape_obs",
            "amount_window",
            "efficiency_window",
            "beta_window",
        ):
            _require_positive(name, getattr(self, name))
        if not self.ffd_vol_windows or any(x <= 0 for x in self.ffd_vol_windows):
            raise AFMLContractError("ffd_vol_windows must contain positive windows")
        if not self.market_vol_windows or any(x <= 0 for x in self.market_vol_windows):
            raise AFMLContractError("market_vol_windows must contain positive windows")
        if self.min_shape_obs > self.shape_window:
            raise AFMLContractError("min_shape_obs cannot exceed shape_window")


@dataclass(frozen=True)
class LabelConfig:
    volatility_method: Literal["ewma_log_return"] = "ewma_log_return"
    volatility_span: int = 60
    min_obs: int = 20
    pt_mult: float = 2.0
    sl_mult: float = 2.0
    vertical_bars: int = 60
    vertical_touch_policy: Literal["sign_return"] = "sign_return"
    zero_return_policy: Literal["drop", "zero_class"] = "drop"
    source_path_kind: Literal["daily_close"] = "daily_close"

    def __post_init__(self) -> None:
        for name in (
            "volatility_span",
            "min_obs",
            "pt_mult",
            "sl_mult",
            "vertical_bars",
        ):
            _require_positive(name, getattr(self, name))
        if self.min_obs > self.volatility_span:
            raise AFMLContractError("min_obs cannot exceed volatility_span")


@dataclass(frozen=True)
class PITConfig:
    timezone: Literal["Asia/Taipei"] = "Asia/Taipei"
    max_environment_staleness_trading_days: int = 1
    decision_cutoff: Literal["after_close"] = "after_close"

    def __post_init__(self) -> None:
        if self.max_environment_staleness_trading_days < 0:
            raise AFMLContractError(
                "max_environment_staleness_trading_days must be non-negative"
            )


@dataclass(frozen=True)
class AFMLConfig:
    schema_version: str = "etf-afml-config-v1"
    dollar_bar: DollarBarConfig = field(default_factory=DollarBarConfig)
    ffd: FFDConfig = field(default_factory=FFDConfig)
    structural: StructuralConfig = field(default_factory=StructuralConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    labels: LabelConfig = field(default_factory=LabelConfig)
    pit: PITConfig = field(default_factory=PITConfig)

    def __post_init__(self) -> None:
        if not self.schema_version.strip():
            raise AFMLContractError("schema_version cannot be empty")


DateLike: TypeAlias = str | date | datetime


def _coerce_date(value: DateLike, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise AFMLContractError(f"{name} must be an ISO date") from exc
    raise AFMLContractError(f"{name} must be a date or ISO date string")


@dataclass(frozen=True)
class AFMLBoundaries:
    train_start: date
    train_end: date
    validation_end: date
    test_end: date

    def __init__(
        self,
        train_start: DateLike,
        train_end: DateLike,
        validation_end: DateLike,
        test_end: DateLike,
    ) -> None:
        values = {
            "train_start": _coerce_date(train_start, "train_start"),
            "train_end": _coerce_date(train_end, "train_end"),
            "validation_end": _coerce_date(validation_end, "validation_end"),
            "test_end": _coerce_date(test_end, "test_end"),
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        if not (
            self.train_start <= self.train_end
            < self.validation_end
            < self.test_end
        ):
            raise AFMLContractError(
                "AFML boundaries must be ordered as "
                "train_start <= train_end < validation_end < test_end"
            )


def validate_run_mode(mode: str) -> AFMLRunMode:
    if mode not in SUPPORTED_AFML_RUN_MODES:
        raise AFMLScopeError(
            f"unsupported AFML mode {mode!r}; expected one of "
            f"{SUPPORTED_AFML_RUN_MODES}"
        )
    return mode  # type: ignore[return-value]


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported config value: {type(value).__name__}")


def config_sha256(config: object) -> str:
    if isinstance(config, type) or not is_dataclass(config):
        raise AFMLContractError("config_sha256 requires a dataclass instance")
    payload = json.dumps(
        asdict(config),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
