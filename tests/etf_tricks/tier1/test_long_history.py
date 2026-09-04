import pandas as pd
import pytest

from etf_tricks.tier1.long_history import validate_long_history_research_frame


def test_long_history_research_frame_rejects_outcomes_in_sealed_interval() -> None:
    frame = pd.DataFrame(
        {
            "t0": pd.to_datetime(["2024-12-20", "2024-12-30"]),
            "t1": pd.to_datetime(["2024-12-31", "2025-01-03"]),
        }
    )

    with pytest.raises(ValueError, match="sealed"):
        validate_long_history_research_frame(
            frame,
            research_t0_end="2024-12-31",
            sealed_start="2025-01-01",
        )


def test_long_history_research_frame_returns_auditable_boundaries() -> None:
    frame = pd.DataFrame(
        {
            "t0": pd.to_datetime(["2024-12-20", "2024-12-30"]),
            "t1": pd.to_datetime(["2024-12-21", "2024-12-31"]),
        }
    )

    result = validate_long_history_research_frame(
        frame,
        research_t0_end="2024-12-31",
        sealed_start="2025-01-01",
    )

    assert result == {
        "research_t0_end": "2024-12-31",
        "sealed_start": "2025-01-01",
        "research_rows": 2,
    }
