from __future__ import annotations

import json
from pathlib import Path

from data_analysts.config import load_runtime_config
from data_analysts.daily_market_state_publication import (
    publish_daily_market_state_partitions,
)
from data_analysts.paths import DataAnalystsContext
from data_analysts.pipeline import _publish_daily_market_state


ROOT = Path(__file__).resolve().parents[1]


def _context(tmp_path: Path) -> tuple[DataAnalystsContext, object]:
    configs = tmp_path / "configs"
    configs.mkdir()
    for source in (ROOT / "configs").glob("*.json"):
        (configs / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    for artifact_id in (
        "security_master",
        "trading_calendar",
        "daily_price_volume",
        "daily_tradability",
    ):
        path = context.store_path("manifests", f"{artifact_id}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "artifact_id": artifact_id,
                    "status": "ready",
                    "active_version": "v1",
                    "date_range": ["2024-01-02", "2024-01-03"],
                    "data_cutoff_at": "2024-01-03T12:00:00Z",
                }
            ),
            encoding="utf-8",
        )
    return context, config


def _row(day: str, ticker: str) -> dict[str, object]:
    return {
        "date": day,
        "ticker": ticker,
        "market_state": "TRADING",
        "amount_state": "OBSERVED",
        "authoritative_traded_value": 100.0,
        "source_available_date": day,
        "earliest_execution_session": day,
        "data_cutoff_at": f"{day}T12:00:00Z",
    }


def test_daily_market_state_bounded_publication_retains_prior_partitions(tmp_path):
    context, config = _context(tmp_path)

    first = publish_daily_market_state_partitions(
        context,
        config,
        (("2024", [_row("2024-01-02", "2330")]),),
        build_start="2024-01-02",
        build_end="2024-01-02",
        certified_source_start="2024-01-02",
        run_scope="full_history",
    )
    second = publish_daily_market_state_partitions(
        context,
        config,
        (("2025", [_row("2025-01-02", "2330")]),),
        build_start="2025-01-02",
        build_end="2025-01-02",
        certified_source_start="2024-01-02",
        run_scope="bounded_backfill",
    )

    assert first.total_row_count == 1
    assert second.total_row_count == 2
    assert second.manifest["date_range"] == ["2024-01-02", "2025-01-02"]
    assert len(second.manifest["artifact_paths"]) == 2
    assert second.manifest["dependency_versions"]["daily_tradability"] == "v1"


def test_pipeline_daily_market_state_builder_publishes_all_dependency_state(tmp_path):
    context, config = _context(tmp_path)

    _publish_daily_market_state(
        context,
        config,
        daily_prices=[
            {"date": "2024-01-02", "ticker": "2330", "traded_value": 100.0},
            {"date": "2024-01-03", "ticker": "2330", "traded_value": 200.0},
        ],
        security_master=[
            {
                "ticker": "2330",
                "market": "TWSE",
                "list_date": "2000-01-01",
                "delist_date": None,
            }
        ],
        trading_calendar_rows=[
            {"date": "2024-01-02", "is_trading_day": True},
            {"date": "2024-01-03", "is_trading_day": True},
            {"date": "2024-01-04", "is_trading_day": True},
        ],
        daily_tradability_rows=[
            {"date": "2024-01-02", "ticker": "2330", "mkt": "TWSE", "stktp_e": "Common Stock"}
        ],
        run_scope="full_history",
        scope_start=None,
        scope_end=None,
    )

    manifest = json.loads(
        context.store_path("manifests", "daily_market_state.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "ready"
    assert manifest["row_count"] == 2
    assert manifest["dependency_manifest_sha256_by_contract"]
