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
        contract_key="daily_price_volume_etf_constituents",
        artifact_id="daily_price_volume_etf_constituents",
        variant="default",
        layer="raw",
        base_path="canonical/raw/daily_price_volume_etf_constituents",
        file_name="part.parquet",
        required_columns=(
            "date", "ticker", "open", "high", "low", "close", "volume",
            "traded_value", "source_available_date", "data_cutoff_at",
        ),
        logical_key=("date", "ticker"),
        publication_mode="partition_upsert",
        partition_name="year",
        partition_field="date",
        date_field="date",
        availability_field="source_available_date",
        pit_policy="source_available_date",
        source_families=("afml_price_snapshot",),
    )


def test_afml_price_snapshot_uses_held_tickers_and_keeps_raw_execution_lineage(tmp_path):
    from data_analysts.afml_price_snapshot import build_and_publish_afml_price_snapshot

    holdings_path = tmp_path / "holdings.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"date": datetime(2020, 1, 2), "ticker": "2330"},
        {"date": datetime(2020, 1, 2), "ticker": "1101"},
        {"date": datetime(2019, 12, 31), "ticker": "9999"},
    ]), holdings_path)
    database = _Database({
        "1101": _Collection([{
            "coid": "1101", "mdate": datetime(2020, 1, 2), "open_d": 40.0,
            "high_d": 42.0, "low_d": 39.0, "close_d": 41.0, "vol": 100.0, "amt": 4100.0,
        }]),
        "2330": _Collection([
            {
                "coid": "2330", "mdate": datetime(2020, 1, 2), "open_d": 300.0,
                "high_d": 305.0, "low_d": 299.0, "close_d": 304.0, "vol": 200.0, "amt": 60800.0,
            },
            {
                "coid": "2330", "mdate": datetime(2021, 1, 4), "open_d": 500.0,
                "high_d": 505.0, "low_d": 499.0, "close_d": 503.0, "vol": 300.0, "amt": 150900.0,
            },
        ]),
    })
    context = DataAnalystsContext.from_paths(tmp_path, tmp_path / "store")

    result = build_and_publish_afml_price_snapshot(
        context,
        contract=_contract(),
        holdings_path=holdings_path,
        database=database,
        family={
            "family_id": "daily_price_volume", "collection_pattern": "{ticker}",
            "source_profile": "large_daily_panel", "primary_key": ["date", "ticker"],
            "date_fields": {"source_date": "mdate"},
            "data_cutoff_policy": "extraction_completed_fallback",
        },
        pit_registry={"families": {}},
        start_date="2020-01-02",
        end_date="2021-01-04",
        extraction_completed_at="2026-09-04T02:00:00Z",
    )

    manifest = json.loads(
        context.store_path("manifests", "daily_price_volume_etf_constituents.json").read_text()
    )
    assert result["ticker_count"] == 2
    assert manifest["row_count"] == 3
    assert manifest["constituent_universe"]["tickers"] == ["1101", "2330"]
    assert manifest["snapshot_kind"] == "afml_etf_constituent_daily_price_volume"
    assert manifest["revision_status"] == "PIT_REVISION_UNVERIFIED"
    assert manifest["source_price_semantics"] == "raw_unadjusted_execution_prices"
    table = pq.read_table(context.artifact_path(manifest["artifact_paths"][0]))
    rows = table.to_pylist()
    assert {"date", "ticker", "open", "high", "low", "close", "volume", "traded_value"} <= set(rows[0])
    assert rows[0]["open"] == 40.0
    assert rows[0]["data_cutoff_at"] == "2026-09-04T02:00:00Z"
    assert len({(row["date"], row["ticker"]) for row in rows}) == len(rows)
