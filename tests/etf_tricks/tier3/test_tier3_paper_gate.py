import pytest

from etf_tricks.tier3.paper_gate import require_sealed_paper_admission


def test_paper_ledger_gate_refuses_research_only_and_not_sealed_lineage() -> None:
    with pytest.raises(ValueError, match="sealed"):
        require_sealed_paper_admission(
            {
                "research_only": True,
                "sealed_status": "NOT_SEALED",
                "tier2_status": "RESEARCH_ONLY",
                "tier3_status": "INSUFFICIENT_CROSS_ETF_UNIVERSE",
            }
        )


def test_paper_ledger_gate_requires_all_sealed_admission_evidence() -> None:
    with pytest.raises(ValueError, match="Tier 3"):
        require_sealed_paper_admission(
            {
                "research_only": False,
                "sealed_status": "SEALED",
                "tier2_status": "SEALED_PASSED",
                "tier3_status": "RESEARCH_ONLY",
            }
        )
