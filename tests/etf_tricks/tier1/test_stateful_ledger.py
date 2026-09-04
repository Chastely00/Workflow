import pandas as pd
import pytest

from etf_tricks.tier1.stateful_ledger import execute_stateful_transitions


def _transitions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["momentum-1", "momentum-2"],
            "etf_id": "momentum",
            "t0_bar_id": [1, 2],
            "decision_available_at": pd.to_datetime(["2024-01-02 13:30", "2024-01-04 13:30"]),
            "transition": ["flat_to_long", "long_to_flat"],
        }
    )


def _opens() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "etf_id": "momentum",
            "date": pd.to_datetime(["2024-01-03", "2024-01-05"]),
            "raw_open_nav": [100.0, 110.0],
            "is_legal_execution": [True, True],
        }
    )


def test_ledger_charges_costs_only_for_real_state_transitions() -> None:
    result = execute_stateful_transitions(_transitions(), _opens(), initial_capital=10_000.0)

    assert result["execution_date"].tolist() == list(pd.to_datetime(["2024-01-03", "2024-01-05"]))
    assert result["side"].tolist() == ["buy", "sell"]
    assert result["shares"].tolist() == [99, -99]
    assert result["commission"].tolist() == pytest.approx([14.1075, 32.67])
    assert result.loc[1, "cash_after"] == pytest.approx(10_943.2225)


def test_ledger_uses_minimum_ticket_fee_and_rejects_non_transition_rows() -> None:
    transitions = _transitions().iloc[:1].copy()
    transitions.loc[0, "transition"] = "flat_to_long"
    result = execute_stateful_transitions(transitions, _opens().iloc[:1], initial_capital=101.0)
    assert result.loc[0, "shares"] == 1
    assert result.loc[0, "commission"] == pytest.approx(1.0)

    invalid = _transitions().copy()
    invalid.loc[0, "transition"] = "not_a_transition"
    with pytest.raises(ValueError, match="transition"):
        execute_stateful_transitions(invalid, _opens(), initial_capital=10_000.0)
