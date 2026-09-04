from __future__ import annotations

from data_analysts.afml_roe_snapshot import resolve_roe_snapshot_rows


def _row(source_row_id: str, r103: float) -> dict[str, object]:
    return {
        "ticker": "2330",
        "no": "A",
        "sem": "3",
        "curr": "NTD",
        "merg": "Y",
        "period_end_date": "2024-09-30",
        "source_available_date": "2024-11-12",
        "revision_date": "2024-09-01",
        "r103": r103,
        "source_row_id": source_row_id,
        "source_collection": "AINVFINB",
        "data_cutoff_at": "2026-09-04T02:30:00Z",
        "data_cutoff_origin": "extraction_completed_fallback",
    }


def test_roe_snapshot_keeps_one_deterministic_value_when_duplicates_agree():
    rows = resolve_roe_snapshot_rows([_row("b", 12.5), _row("a", 12.5)])

    assert len(rows) == 1
    assert rows[0]["r103"] == 12.5
    assert rows[0]["r103_conflict"] is False
    assert rows[0]["source_row_count"] == 2
    assert rows[0]["source_row_id"] == "a"


def test_roe_snapshot_marks_conflicting_duplicate_value_missing():
    rows = resolve_roe_snapshot_rows([_row("a", 12.5), _row("b", 13.0)])

    assert len(rows) == 1
    assert rows[0]["r103"] is None
    assert rows[0]["r103_conflict"] is True
    assert rows[0]["source_row_count"] == 2
