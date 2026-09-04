from datetime import datetime
import json

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.artifact_contracts import ArtifactContract
from data_analysts.paths import DataAnalystsContext


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query=None):
        return list(self.rows)


class _Database:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]

    def list_collection_names(self):
        return list(self.collections)


def _contract():
    return ArtifactContract(
        contract_key="daily_chip_etf_constituents",
        artifact_id="daily_chip_etf_constituents",
        variant="default",
        layer="raw",
        base_path="canonical/raw/daily_chip_etf_constituents",
        file_name="part.parquet",
        required_columns=("date", "ticker", "source_available_date", "data_cutoff_at"),
        logical_key=("date", "ticker"),
        publication_mode="partition_upsert",
        partition_name="year",
        partition_field="date",
        date_field="date",
        availability_field="source_available_date",
        pit_policy="source_available_date",
        source_families=("daily_chip",),
    )


def test_afml_chip_snapshot_uses_holdings_universe_and_writes_lineage(tmp_path):
    from data_analysts.afml_chip_snapshot import build_and_publish_afml_chip_snapshot

    holdings_path = tmp_path / "holdings.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"date": datetime(2024, 1, 2), "ticker": "2330"},
        {"date": datetime(2024, 1, 2), "ticker": "1101"},
        {"date": datetime(2023, 12, 29), "ticker": "9999"},
    ]), holdings_path)
    database = _Database({
        "1101": _Collection([{"coid": "1101", "mdate": datetime(2024, 1, 2), "qfii_examt": 10.0}]),
        "2330": _Collection([{"coid": "2330", "mdate": datetime(2024, 1, 2), "qfii_examt": 20.0}]),
    })
    context = DataAnalystsContext.from_paths(tmp_path, tmp_path / "store")

    result = build_and_publish_afml_chip_snapshot(
        context,
        contract=_contract(),
        holdings_path=holdings_path,
        database=database,
        family={
            "family_id": "daily_chip", "collection_pattern": "{ticker}",
            "source_profile": "large_daily_panel", "primary_key": ["date", "ticker"],
            "date_fields": {"source_date": "mdate"},
            "data_cutoff_policy": "extraction_completed_fallback",
        },
        pit_registry={"families": {}},
        start_date="2024-01-02",
        end_date="2024-01-02",
        extraction_completed_at="2026-09-04T01:40:00Z",
    )

    manifest = json.loads(context.store_path("manifests", "daily_chip_etf_constituents.json").read_text())
    assert result["ticker_count"] == 2
    assert manifest["row_count"] == 2
    assert manifest["constituent_universe"]["tickers"] == ["1101", "2330"]
    assert manifest["revision_status"] == "PIT_REVISION_UNVERIFIED"
    assert all("/versions/" in path for path in manifest["artifact_paths"])
