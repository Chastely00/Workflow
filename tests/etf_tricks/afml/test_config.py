from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from etf_tricks.afml import (
    AFMLBoundaries,
    AFMLConfig,
    DollarBarConfig,
    FFDConfig,
    LabelConfig,
    StructuralConfig,
    config_sha256,
    validate_run_mode,
)


def test_default_config_matches_approved_contract():
    config = AFMLConfig()

    assert config.dollar_bar.market_amount_lookback_days == 60
    assert config.dollar_bar.min_market_amount_observations == 20
    assert config.dollar_bar.candidate_quantile_count == 99
    assert config.dollar_bar.min_completed_bars == 120
    assert config.dollar_bar.max_bar_duration_trading_days == 60
    assert config.ffd.weight_tolerance == 1e-5
    assert config.ffd.coarse_step == 0.05
    assert config.ffd.refine_step == 0.01
    assert config.ffd.autonomous_max_d == 5.0
    assert config.structural.q == 0.95
    assert config.structural.v == 0.025
    assert config.labels.pt_mult == config.labels.sl_mult == 2.0
    assert config.labels.vertical_bars == 60
    assert len(config_sha256(config)) == 64
    with pytest.raises(FrozenInstanceError):
        config.labels.vertical_bars = 5


def test_config_hash_is_deterministic_and_changes_with_contract():
    config = AFMLConfig()
    changed = replace(config, labels=replace(config.labels, vertical_bars=40))

    assert config_sha256(config) == config_sha256(AFMLConfig())
    assert config_sha256(config) != config_sha256(changed)


def test_train_boundaries_are_explicit_normalized_and_ordered():
    boundaries = AFMLBoundaries(
        train_start="2020-01-01",
        train_end="2023-12-31",
        validation_end="2024-12-31",
        test_end="2026-07-07",
    )

    assert boundaries.train_start == date(2020, 1, 1)
    assert boundaries.train_end < boundaries.validation_end < boundaries.test_end
    with pytest.raises(ValueError, match="ordered"):
        AFMLBoundaries("2024-01-01", "2025-01-01", "2024-12-31", "2026-01-01")


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: DollarBarConfig(market_amount_lookback_days=0), "positive"),
        (lambda: DollarBarConfig(max_bars_per_day=2), "max_bars_per_day"),
        (
            lambda: DollarBarConfig(emit_incomplete_terminal_bar=True),
            "emit_incomplete_terminal_bar",
        ),
        (lambda: FFDConfig(coarse_step=0), "positive"),
        (lambda: FFDConfig(autonomous_max_d=0.5), "autonomous_max_d"),
        (lambda: StructuralConfig(q=1.0), "0 < q < 1"),
        (lambda: StructuralConfig(q=0.95, v=0.1), "v"),
        (lambda: LabelConfig(vertical_bars=0), "positive"),
    ],
)
def test_invalid_config_values_fail_closed(factory, message):
    with pytest.raises(ValueError, match=message):
        factory()


def test_run_mode_validation_rejects_unknown_scope():
    assert validate_run_mode("walk_forward") == "walk_forward"
    with pytest.raises(ValueError, match="unsupported AFML mode"):
        validate_run_mode("full_history")
