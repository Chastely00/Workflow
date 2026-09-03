from __future__ import annotations

from datetime import datetime


class _Collection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def find(self, query, projection):
        self.calls += 1
        return list(self.rows)


class _Database:
    def __init__(self, collections):
        self.collections = collections

    def list_collection_names(self):
        return list(self.collections)

    def __getitem__(self, name):
        return self.collections[name]


def test_bounded_tradability_extracts_once_per_ticker_and_sorts() -> None:
    from data_analysts.tej_tradability import extract_bounded_tradability_rows

    collections = {
        "1101": _Collection([
            {"coid": "1101", "mdate": datetime(2024, 1, 3), "mkt": "TSE", "stktp_e": "Common Stock"},
            {"coid": "1101", "mdate": datetime(2023, 12, 29), "mkt": "TSE", "stktp_e": "Common Stock"},
        ]),
        "6488": _Collection([
            {"coid": "6488", "mdate": datetime(2024, 1, 2), "mkt": "OTC", "stktp_e": "Common Stock"},
        ]),
    }
    rows, metrics = extract_bounded_tradability_rows(
        _Database(collections), start_date="2024-01-02", end_date="2024-01-03", workers=2
    )

    assert [(row["date"], row["ticker"]) for row in rows] == [
        ("2024-01-02", "6488"), ("2024-01-03", "1101"),
    ]
    assert [collection.calls for collection in collections.values()] == [1, 1]
    assert metrics["collection_count"] == 2


def test_bounded_tradability_publishes_copy_on_write_year_partitions(tmp_path) -> None:
    from data_analysts.paths import DataAnalystsContext
    from data_analysts.tej_tradability import publish_bounded_tradability_rows

    context = DataAnalystsContext.from_paths("DataAnalysts", tmp_path)
    rows = [
        {"date": "2024-01-02", "ticker": "1101", "source_available_date": "2024-01-02", "source_collection": "1101"},
        {"date": "2025-01-02", "ticker": "1101", "source_available_date": "2025-01-02", "source_collection": "1101"},
    ]
    result = publish_bounded_tradability_rows(
        context, rows, build_start="2024-01-02", build_end="2025-01-02",
        data_cutoff_at="2026-09-03T00:00:00Z",
    )
    manifest = result["manifest"]
    assert manifest["row_count"] == 2
    assert len(manifest["artifact_paths"]) == 2
    assert all("/versions/" in path for path in manifest["artifact_paths"])
    assert context.store_path("manifests", "daily_tradability.json").is_file()
