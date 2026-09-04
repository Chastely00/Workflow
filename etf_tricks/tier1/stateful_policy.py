"""PIT-safe conversion of ETF-local Tier 1 OOF evidence into position states."""

from __future__ import annotations

import numpy as np
import pandas as pd


_REQUIRED = {"event_id", "etf_id", "t0_bar_id", "p1", "prediction_kind", "decision_available_at"}
_FORBIDDEN_FUTURE_COLUMNS = {
    "t1",
    "y_direction",
    "target_status",
    "trigger_date",
    "entry_date",
    "entry_raw_open",
    "exit_date",
    "exit_raw_open",
    "net_log_return",
}


def build_stateful_transitions(oof: pd.DataFrame, *, entry_score: float, exit_score: float) -> pd.DataFrame:
    """Aggregate calibrated OOF probabilities into non-overlapping flat/long transitions.

    Every completed Dollar bar contributes ``p1 - 0.5`` evidence.  Position
    changes are deliberately sparse: evidence is reset only after a real state
    transition, so a long position cannot open repeatedly.  This function
    accepts no realized labels, horizons, prices, or outcomes.
    """
    if missing := _REQUIRED.difference(oof.columns):
        raise ValueError(f"OOF input missing columns: {sorted(missing)}")
    if forbidden := _FORBIDDEN_FUTURE_COLUMNS.intersection(oof.columns):
        raise ValueError(f"OOF input contains future-outcome leakage columns: {sorted(forbidden)}")
    if not np.isfinite(entry_score) or entry_score <= 0:
        raise ValueError("entry_score must be finite and positive")
    if not np.isfinite(exit_score) or exit_score >= 0:
        raise ValueError("exit_score must be finite and negative")
    if oof.empty:
        raise ValueError("OOF input is empty")
    if oof["etf_id"].nunique(dropna=False) != 1:
        raise ValueError("stateful policy accepts one ETF-local OOF stream only")
    if oof[["event_id", "t0_bar_id", "decision_available_at"]].isna().any().any():
        raise ValueError("OOF keys and decision availability must be present")
    if oof["event_id"].duplicated().any() or oof["t0_bar_id"].duplicated().any():
        raise ValueError("OOF event_id and t0_bar_id must be unique")
    if not oof["prediction_kind"].eq("OOF_CALIBRATED").all():
        raise ValueError("stateful policy accepts calibrated OOF predictions only")
    p1 = pd.to_numeric(oof["p1"], errors="coerce")
    if not np.isfinite(p1).all() or ((p1 < 0) | (p1 > 1)).any():
        raise ValueError("p1 must be finite probabilities in [0, 1]")
    bars = pd.to_numeric(oof["t0_bar_id"], errors="raise")
    if not bars.is_monotonic_increasing or (bars.diff().iloc[1:] <= 0).any():
        raise ValueError("t0_bar_id must be strictly increasing in decision order")
    availability = pd.to_datetime(oof["decision_available_at"], errors="raise")
    if not availability.is_monotonic_increasing:
        raise ValueError("decision_available_at must be increasing in decision order")

    rows: list[dict[str, object]] = []
    state = "flat"
    score = 0.0
    for row, probability in zip(oof.itertuples(index=False), p1, strict=True):
        before = score
        score += float(probability) - 0.5
        transition: str | None = None
        state_before = state
        if state == "flat" and (score > entry_score or np.isclose(score, entry_score, atol=1e-12, rtol=0.0)):
            transition = "flat_to_long"
            state = "long"
            score = 0.0
        elif state == "long" and (score < exit_score or np.isclose(score, exit_score, atol=1e-12, rtol=0.0)):
            transition = "long_to_flat"
            state = "flat"
            score = 0.0
        rows.append(
            {
                "event_id": row.event_id,
                "etf_id": row.etf_id,
                "t0_bar_id": row.t0_bar_id,
                "decision_available_at": row.decision_available_at,
                "p1": float(probability),
                "signal_delta": float(probability) - 0.5,
                "evidence_score_before": before,
                "evidence_score_after": score,
                "state_before": state_before,
                "state_after": state,
                "transition": transition,
            }
        )
    return pd.DataFrame(rows)
