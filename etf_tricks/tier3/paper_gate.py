"""Hard admission boundary before constituent paper execution."""

from __future__ import annotations


def require_sealed_paper_admission(admission: dict[str, object]) -> None:
    """Reject every research-only, unsealed or incomplete Tier 3 lineage."""
    if admission.get("research_only") is not False or admission.get("sealed_status") != "SEALED":
        raise ValueError("constituent paper ledger requires sealed, non-research admission")
    if admission.get("tier2_status") != "SEALED_PASSED":
        raise ValueError("constituent paper ledger requires sealed Tier 2 admission")
    if admission.get("tier3_status") != "SEALED_PASSED":
        raise ValueError("constituent paper ledger requires sealed Tier 3 admission")
