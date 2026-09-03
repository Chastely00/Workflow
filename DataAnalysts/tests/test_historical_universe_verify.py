import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.config import load_runtime_config
from data_analysts.metadata import publish_data_store_metadata
from data_analysts.paths import DataAnalystsContext
from data_analysts.verify import verify_runtime


ROOT = Path(__file__).resolve().parents[1]


def _copy_configs(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
        "artifact_contracts.json",
    ]:
        (tmp_path / "configs" / name).write_text(
            (ROOT / "configs" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    source_family_profiles_path = tmp_path / "configs" / "source_family_profiles.json"
    source_family_profiles = json.loads(source_family_profiles_path.read_text(encoding="utf-8"))
    for family in source_family_profiles.get("families", []):
        if isinstance(family, dict):
            family["enabled"] = False
    source_family_profiles_path.write_text(
        json.dumps(source_family_profiles, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _publish_metadata(context: DataAnalystsContext) -> None:
    config = load_runtime_config(context)
    publish_data_store_metadata(context, config)


def _base_manifest(rows: list[dict[str, object]]) -> dict[str, object]:
    schema = pa.Table.from_pylist(rows).schema
    as_of_dates = sorted(str(row["as_of_date"]) for row in rows if row.get("as_of_date"))
    effective_dates = sorted(
        str(row["effective_date"]) for row in rows if row.get("effective_date")
    )
    return {
        "artifact_id": "universe_tw_equity_liquid_top500",
        "contract_key": "universe_tw_equity_liquid_top500:historical",
        "variant": "historical",
        "schema_version": "1.0",
        "layer": "derived",
        "source_families": ["security_panel_history"],
        "source_collections": [],
        "row_count": len(rows),
        "date_range": [as_of_dates[0], as_of_dates[-1]] if as_of_dates else None,
        "availability_date_range": (
            [effective_dates[0], effective_dates[-1]] if effective_dates else None
        ),
        "columns": schema.names,
        "schema_fingerprint": __import__("hashlib").sha256(
            schema.serialize().to_pybytes()
        ).hexdigest(),
        "partitioning": ["as_of_year"],
        "artifact_paths": [
            "canonical/derived/universes/tw_equity_liquid_top500/membership_by_year/as_of_year=2025/part.parquet"
        ],
        "pit_policy": "effective_next_trading_day_membership",
        "data_cutoff_at": max(str(row["data_cutoff_at"]) for row in rows),
        "duplicate_count": 0,
        "omitted_row_count": 0,
        "status": "ready",
        "created_at": "2026-07-07T00:00:00Z",
    }


def _write_universe_fixture(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    diagnostics_rows: list[dict[str, object]] | None = None,
    extra_manifest_paths: list[str] | None = None,
) -> None:
    artifact = (
        tmp_path
        / "data_store"
        / "canonical"
        / "derived"
        / "universes"
        / "tw_equity_liquid_top500"
        / "membership_by_year"
        / "as_of_year=2025"
        / "part.parquet"
    )
    artifact.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), artifact)

    if extra_manifest_paths:
        for relative_path in extra_manifest_paths:
            extra_artifact = tmp_path / "data_store" / Path(relative_path)
            extra_artifact.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(rows[:1]), extra_artifact)

    if diagnostics_rows is not None:
        diagnostics_path = (
            tmp_path
            / "data_store"
            / "canonical"
            / "derived"
            / "universes"
            / "tw_equity_liquid_top500"
            / "diagnostics"
            / "diagnostics.parquet"
        )
        diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(diagnostics_rows), diagnostics_path)

    manifests = tmp_path / "data_store" / "manifests"
    manifests.mkdir(parents=True)
    manifest = _base_manifest(rows)
    if extra_manifest_paths:
        manifest["artifact_paths"] = [*manifest["artifact_paths"], *extra_manifest_paths]
    (manifests / "universe_tw_equity_liquid_top500.historical.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _set_universe_limit(tmp_path: Path, universe_id: str, limit: int) -> None:
    config_path = tmp_path / "configs" / "universe_specs.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    for spec in payload.get("universes", []):
        if isinstance(spec, dict) and spec.get("universe_id") == universe_id:
            spec["limit"] = limit
            break
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _historical_universe_diagnostics_row(
    *,
    as_of_date_count: int = 1,
    candidate_count: int = 2,
    included_count: int = 2,
    excluded_count: int = 0,
    top_n_limit: int = 500,
    max_included_count: int = 2,
    top_n_underfilled_date_count: int = 0,
    duplicate_universe_effective_ticker_count: int = 0,
    duplicate_universe_effective_rank_count: int = 0,
) -> dict[str, object]:
    return {
        "universe_id": "tw_equity_liquid_top500",
        "as_of_date_count": as_of_date_count,
        "candidate_count": candidate_count,
        "included_count": included_count,
        "excluded_count": excluded_count,
        "top_n_limit": top_n_limit,
        "max_included_count": max_included_count,
        "top_n_underfilled_date_count": top_n_underfilled_date_count,
        "duplicate_universe_effective_ticker_count": duplicate_universe_effective_ticker_count,
        "duplicate_universe_effective_rank_count": duplicate_universe_effective_rank_count,
    }


def _historical_row(
    *,
    as_of_date: str = "2025-01-02",
    effective_date: str = "2025-01-03",
    ticker: str = "2330",
    rank: int = 1,
    included: bool = True,
    reason: str = "selected",
) -> dict[str, object]:
    return {
        "as_of_date": as_of_date,
        "effective_date": effective_date,
        "universe_id": "tw_equity_liquid_top500",
        "ticker": ticker,
        "rank": rank,
        "included": included,
        "reason": reason,
        "data_cutoff_at": f"{effective_date}T12:00:00Z",
    }


def test_verify_blocks_historical_universe_same_day_effective_date(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(effective_date="2025-01-02")],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "effective_date <= as_of_date" in result["message"]


def test_verify_blocks_historical_universe_missing_required_field(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    rows = [_historical_row()]
    rows[0]["ticker"] = ""
    _write_universe_fixture(tmp_path, rows)

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "missing required fields" in result["message"]
    assert "ticker" in result["message"]


def test_verify_blocks_historical_universe_duplicate_effective_membership_key(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [
            _historical_row(as_of_date="2025-01-02", effective_date="2025-01-06", ticker="2330", rank=1),
            _historical_row(as_of_date="2025-01-03", effective_date="2025-01-06", ticker="2330", rank=2),
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "duplicate historical universe effective membership key" in result["message"]


def test_verify_blocks_historical_universe_small_file_daily_partition(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [_historical_row()],
        extra_manifest_paths=[
            "canonical/derived/universes/tw_equity_liquid_top500/membership_by_date/as_of_date=2025-01-02/membership.parquet"
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "small_file_daily_partition_count > 0" in result["message"]


def test_verify_blocks_historical_universe_top_n_overflow_per_effective_date(tmp_path):
    _copy_configs(tmp_path)
    _set_universe_limit(tmp_path, "tw_equity_liquid_top500", 2)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [
            _historical_row(ticker="1101", rank=1),
            _historical_row(ticker="1216", rank=2),
            _historical_row(ticker="1301", rank=3),
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "row_count per effective_date exceeds top-n limit" in result["message"]


def test_verify_blocks_historical_universe_top_n_underfilled_when_diagnostics_show_enough_candidates(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(ticker="1101", rank=1)],
        diagnostics_rows=[
            {
                "universe_id": "tw_equity_liquid_top500",
                "as_of_date_count": 1,
                "candidate_count": 600,
                "included_count": 1,
                "excluded_count": 599,
                "top_n_limit": 500,
                "max_included_count": 1,
                "top_n_underfilled_date_count": 1,
                "duplicate_universe_effective_ticker_count": 0,
                "duplicate_universe_effective_rank_count": 0,
            }
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "underfilled top-n dates" in result["message"]


def test_verify_blocks_historical_universe_multi_day_top_n_underfill_diagnostics(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [
            _historical_row(as_of_date="2025-01-02", effective_date="2025-01-03", ticker=f"t{index}", rank=index)
            for index in range(1, 501)
        ]
        + [
            _historical_row(as_of_date="2025-01-03", effective_date="2025-01-06", ticker="u1", rank=1),
        ],
        diagnostics_rows=[
            {
                "universe_id": "tw_equity_liquid_top500",
                "as_of_date_count": 2,
                "candidate_count": 1000,
                "included_count": 501,
                "excluded_count": 499,
                "top_n_limit": 500,
                "max_included_count": 500,
                "top_n_underfilled_date_count": 1,
                "duplicate_universe_effective_ticker_count": 0,
                "duplicate_universe_effective_rank_count": 0,
            }
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "underfilled top-n dates" in result["message"]


def test_verify_blocks_historical_universe_nonzero_duplicate_diagnostics(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(ticker="1101", rank=1)],
        diagnostics_rows=[
            {
                "universe_id": "tw_equity_liquid_top500",
                "as_of_date_count": 2,
                "candidate_count": 4,
                "included_count": 1,
                "excluded_count": 3,
                "top_n_limit": 500,
                "max_included_count": 1,
                "top_n_underfilled_date_count": 0,
                "duplicate_universe_effective_ticker_count": 1,
                "duplicate_universe_effective_rank_count": 0,
            }
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "duplicate diagnostics counters" in result["message"]


def test_verify_blocks_historical_universe_missing_diagnostics_for_top_n(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(ticker="1101", rank=1)],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "missing diagnostics" in result["message"]


def test_verify_blocks_historical_universe_missing_required_diagnostic_counter(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(ticker="1101", rank=1)],
        diagnostics_rows=[
            {
                "universe_id": "tw_equity_liquid_top500",
                "as_of_date_count": 1,
                "candidate_count": 1,
                "included_count": 1,
                "excluded_count": 0,
                "top_n_limit": 500,
                "max_included_count": 1,
                "duplicate_universe_effective_ticker_count": 0,
                "duplicate_universe_effective_rank_count": 0,
            }
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "missing required diagnostics counters" in result["message"]


def test_verify_blocks_historical_universe_invalid_diagnostic_counter_type(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(ticker="1101", rank=1)],
        diagnostics_rows=[
            {
                "universe_id": "tw_equity_liquid_top500",
                "as_of_date_count": 1,
                "candidate_count": 1,
                "included_count": 1,
                "excluded_count": 0,
                "top_n_limit": 500,
                "max_included_count": 1,
                "top_n_underfilled_date_count": "0",
                "duplicate_universe_effective_ticker_count": 0,
                "duplicate_universe_effective_rank_count": 0,
            }
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "invalid diagnostics counter type" in result["message"]


def test_verify_blocks_historical_universe_bool_diagnostic_counter_type(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(ticker="1101", rank=1)],
        diagnostics_rows=[
            {
                "universe_id": "tw_equity_liquid_top500",
                "as_of_date_count": 1,
                "candidate_count": 1,
                "included_count": 1,
                "excluded_count": 0,
                "top_n_limit": 500,
                "max_included_count": False,
                "top_n_underfilled_date_count": 0,
                "duplicate_universe_effective_ticker_count": 0,
                "duplicate_universe_effective_rank_count": 0,
            }
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "invalid diagnostics counter type" in result["message"]


def test_verify_blocks_historical_universe_missing_core_diagnostic_counter(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(ticker="1101", rank=1)],
        diagnostics_rows=[
            {
                "universe_id": "tw_equity_liquid_top500",
                "as_of_date_count": 1,
                "included_count": 1,
                "excluded_count": 0,
                "top_n_limit": 500,
                "max_included_count": 1,
                "top_n_underfilled_date_count": 0,
                "duplicate_universe_effective_ticker_count": 0,
                "duplicate_universe_effective_rank_count": 0,
            }
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "missing required diagnostics counters" in result["message"]
    assert "candidate_count" in result["message"]


def test_verify_blocks_historical_universe_multiple_diagnostic_rows(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [_historical_row(ticker="1101", rank=1)],
        diagnostics_rows=[
            {
                "universe_id": "tw_equity_liquid_top500",
                "as_of_date_count": 1,
                "candidate_count": 1,
                "included_count": 1,
                "excluded_count": 0,
                "top_n_limit": 500,
                "max_included_count": 1,
                "top_n_underfilled_date_count": 0,
                "duplicate_universe_effective_ticker_count": 0,
                "duplicate_universe_effective_rank_count": 0,
            },
            {
                "universe_id": "tw_equity_liquid_top500",
                "as_of_date_count": 1,
                "candidate_count": 1,
                "included_count": 1,
                "excluded_count": 0,
                "top_n_limit": 500,
                "max_included_count": 1,
                "top_n_underfilled_date_count": 1,
                "duplicate_universe_effective_ticker_count": 0,
                "duplicate_universe_effective_rank_count": 0,
            },
        ],
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "historical_universe"
    assert "must have exactly one diagnostics row" in result["message"]


def test_verify_blocks_security_panel_history_null_effective_date(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    artifact_path = (
        tmp_path
        / "data_store"
        / "canonical"
        / "derived"
        / "security_panel_history"
        / "as_of_year=2025"
        / "part.parquet"
    )
    artifact_path.parent.mkdir(parents=True)
    rows = [
        {
            "as_of_date": "2025-01-03",
            "effective_date": None,
            "source_max_date": "2025-01-03",
            "ticker": "2330",
            "tradable": True,
            "adj_close": 100,
            "market_cap": 10000,
            "adv20": 1000,
            "data_cutoff_at": "2025-01-03T00:00:00Z",
        }
    ]
    pq.write_table(pa.Table.from_pylist(rows), artifact_path)
    manifests = tmp_path / "data_store" / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "security_panel_history.json").write_text(
        json.dumps(
            {
                "artifact_id": "security_panel_history",
                "schema_version": "1.0",
                "layer": "derived",
                "source_families": ["daily_price_volume", "security_master", "trading_calendar"],
                "source_collections": [],
                "row_count": 1,
                "date_range": ["2025-01-03", "2025-01-03"],
                "availability_date_range": [None, None],
                "columns": list(rows[0].keys()),
                "partitioning": ["as_of_year"],
                "artifact_paths": [
                    "canonical/derived/security_panel_history/as_of_year=2025/part.parquet"
                ],
                "pit_policy": "effective_next_trading_day_panel",
                "data_cutoff_at": "2025-01-03T00:00:00Z",
                "duplicate_count": 0,
                "omitted_row_count": 0,
                "status": "ready",
                "created_at": "2026-07-07T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "security_panel_history"
    assert "missing required fields" in result["message"]


def test_verify_uses_snapshot_universe_specs_after_metadata_publish_despite_live_config_drift(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    _publish_metadata(context)
    _write_universe_fixture(
        tmp_path,
        [
            _historical_row(ticker="1101", rank=1),
            _historical_row(ticker="1216", rank=2),
        ],
        diagnostics_rows=[_historical_universe_diagnostics_row()],
    )
    _set_universe_limit(tmp_path, "tw_equity_liquid_top500", 1)

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "artifact_inventory"
    assert "active_version" in result["message"]
