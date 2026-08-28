from __future__ import annotations

from etf_tricks.afml.lab import _resolve_core_readiness_status


def test_verified_revision_with_undeclared_calendar_coverage_stays_bounded() -> None:
    status = _resolve_core_readiness_status(
        core_ready=True,
        revision_status="PIT_REVISION_VERIFIED",
        trading_calendar_manifest_coverage_declared=False,
        mode="train",
    )

    assert status == "CORE_READY_FOR_BOUNDED_RESEARCH_WITH_LIMITATIONS"
