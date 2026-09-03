import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.config import load_runtime_config
from data_analysts.inspect import inspect_artifacts
from data_analysts.paths import DataAnalystsContext
from data_analysts.pipeline import run_pipeline
from data_analysts.store_audit import audit_store
from data_analysts.verify import verify_runtime


def _active_path(context: DataAnalystsContext, manifest_name: str, token: str) -> Path:
    manifest = json.loads(
        context.store_path("manifests", manifest_name).read_text(encoding="utf-8")
    )
    matches = [path for path in manifest["artifact_paths"] if token in path]
    assert len(matches) == 1
    return context.artifact_path(matches[0])


def _write_configs(root: Path, *, raw_historical_shapes: bool = False) -> None:
    source = Path(__file__).resolve().parents[1] / "configs"
    target = root / "configs"
    target.mkdir(parents=True)
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
        "artifact_contracts.json",
    ]:
        payload = json.loads((source / name).read_text(encoding="utf-8"))
        if name == "source_family_profiles.json":
            payload["families"] = [
                {
                    "family_id": "daily_price_volume",
                    "enabled": True,
                    "connection": "apiprcd",
                    "collection_pattern": "{ticker}",
                    "source_profile": "large_daily_panel",
                    "primary_key": ["date", "ticker"],
                    "date_fields": {"source_date": "mdate"},
                    "availability": {"type": "same_day_after_close", "field": "mdate"},
                    "partitioning": ["year"],
                    "pit_policy": "source_date_lagged_to_decision_date",
                    "field_map": {
                        "date": "mdate",
                        "ticker": "coid",
                        "open": "open_d",
                        "high": "high_d",
                        "low": "low_d",
                        "close": "close_d",
                        "volume": "vol",
                        "traded_value": "amt",
                        "market_cap": "mktcap",
                        "data_cutoff_at": "data_cutoff_at",
                    },
                    "fixture_rows": [
                        {
                            "coid": "2330",
                            "mdate": "2025-01-02",
                            "open_d": 100,
                            "high_d": 101,
                            "low_d": 99,
                            "close_d": 100,
                            "vol": 10,
                            "amt": 20000000,
                            "mktcap": 500000000,
                            "data_cutoff_at": "2025-01-02T00:00:00Z",
                        },
                        {
                            "coid": "2330",
                            "mdate": "2025-01-03",
                            "open_d": 101,
                            "high_d": 102,
                            "low_d": 100,
                            "close_d": 101,
                            "vol": 11,
                            "amt": 22000000,
                            "mktcap": 510000000,
                            "data_cutoff_at": "2025-01-03T00:00:00Z",
                        },
                    ],
                },
                {
                    "family_id": "security_master",
                    "enabled": True,
                    "connection": "tej",
                    "collection": "APISTOCK",
                    "source_profile": "small_snapshot",
                    "primary_key": ["ticker"],
                    "date_fields": {},
                    "availability": {"type": "snapshot_as_of_cutoff"},
                    "partitioning": ["single_file"],
                    "pit_policy": "snapshot_cutoff",
                    "field_map": {
                        "ticker": "coid",
                        "stock_name": "stk_name",
                        "market": "mkt",
                        "security_type": "stktp_e",
                        "list_date": "list_date",
                        "data_cutoff_at": "data_cutoff_at",
                    },
                    "fixture_rows": [
                        {
                            "coid": "2330",
                            "stk_name": "TSMC",
                            "mkt": "TWSE",
                            "stktp_e": "common_stock",
                            "list_date": "2025-01-02",
                            "data_cutoff_at": "2025-01-01T00:00:00Z",
                        }
                    ],
                },
                {
                    "family_id": "trading_calendar",
                    "enabled": True,
                    "connection": "tej",
                    "collection": "TRADEDAY_TWSE",
                    "source_profile": "small_snapshot",
                    "primary_key": ["date", "market"],
                    "date_fields": {"source_date": "zdate"},
                    "availability": {"type": "source_available_date", "field": "zdate"},
                    "partitioning": ["single_file"],
                    "pit_policy": "source_available_date",
                    "fixture_rows": [
                        *(
                            [
                                {
                                    "zdate": "2025-01-02",
                                    "mkt": "TWSE",
                                    "date_rmk": "",
                                    "data_cutoff_at": "2025-01-02T00:00:00Z",
                                },
                                {
                                    "zdate": "2025-01-03",
                                    "mkt": "TWSE",
                                    "date_rmk": "",
                                    "data_cutoff_at": "2025-01-03T00:00:00Z",
                                },
                                {
                                    "zdate": "2025-01-04",
                                    "mkt": "TWSE",
                                    "date_rmk": "休市",
                                    "data_cutoff_at": "2025-01-04T00:00:00Z",
                                },
                                {
                                    "zdate": "2025-01-06",
                                    "mkt": "TWSE",
                                    "date_rmk": "",
                                    "data_cutoff_at": "2025-01-06T00:00:00Z",
                                },
                            ]
                            if raw_historical_shapes
                            else [
                                {
                                    "zdate": "2025-01-02",
                                    "mkt": "TWSE",
                                    "date_rmk": "",
                                    "date": "2025-01-02",
                                    "market": "TWSE",
                                    "is_trading_day": True,
                                    "data_cutoff_at": "2025-01-02T00:00:00Z",
                                },
                                {
                                    "zdate": "2025-01-03",
                                    "mkt": "TWSE",
                                    "date_rmk": "",
                                    "date": "2025-01-03",
                                    "market": "TWSE",
                                    "is_trading_day": True,
                                    "data_cutoff_at": "2025-01-03T00:00:00Z",
                                },
                                {
                                    "zdate": "2025-01-06",
                                    "mkt": "TWSE",
                                    "date_rmk": "",
                                    "date": "2025-01-06",
                                    "market": "TWSE",
                                    "is_trading_day": True,
                                    "data_cutoff_at": "2025-01-06T00:00:00Z",
                                },
                            ]
                        ),
                    ],
                },
            ]
        (target / name).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_pipeline_publishes_historical_universe_memberships_by_year(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    result = run_pipeline(
        context,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )

    assert result["status"] == "verifying"

    security_panel_path = _active_path(
        context, "security_panel_history.json", "as_of_year=2025"
    )
    membership_path = _active_path(
        context, "universe_tw_equity_liquid_top500.historical.json", "as_of_year=2025"
    )
    assert security_panel_path.exists()
    assert membership_path.exists()
    assert not list(
        (
            tmp_path
            / "data_store"
            / "canonical"
            / "derived"
            / "universes"
            / "tw_equity_liquid_top500"
        ).glob("membership_by_date/as_of_date=*/membership.parquet")
    )

    membership_rows = pq.read_table(membership_path).to_pylist()
    assert {(row["as_of_date"], row["effective_date"], row["ticker"]) for row in membership_rows} == {
        ("2025-01-02", "2025-01-03", "2330"),
        ("2025-01-03", "2025-01-06", "2330"),
    }


def test_full_history_universe_uses_only_fresh_recomputed_domain(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    run_pipeline(
        context,
        load_runtime_config(context),
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )

    profile_path = tmp_path / "configs" / "source_family_profiles.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    for family in payload["families"]:
        for row in family.get("fixture_rows", []):
            if row.get("coid") == "2330":
                row["coid"] = "2317"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    run_pipeline(
        context,
        load_runtime_config(context),
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )

    membership_path = _active_path(
        context,
        "universe_tw_equity_liquid_top500.historical.json",
        "as_of_year=2025",
    )
    rows = pq.read_table(membership_path).to_pylist()
    assert {row["ticker"] for row in rows} == {"2317"}

    diagnostics_path = (
        tmp_path
        / "data_store"
        / "diagnostics"
        / "historical_universe"
        / "tw_equity_liquid_top500.json"
    )
    assert diagnostics_path.exists()

    manifest = json.loads(
        (
            tmp_path
            / "data_store"
                / "manifests"
                / "universe_tw_equity_liquid_top500.historical.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["partitioning"] == ["as_of_year"]
    assert manifest["pit_policy"] == "effective_next_trading_day_membership"
    assert manifest["date_range"] == ["2025-01-02", "2025-01-03"]
    assert manifest["source_families"] == ["security_panel_history"]

    security_panel_manifest = json.loads(
        (
            tmp_path
            / "data_store"
            / "manifests"
            / "security_panel_history.json"
        ).read_text(encoding="utf-8")
    )
    assert security_panel_manifest["source_families"] == [
        "daily_price_volume",
        "security_master",
        "trading_calendar",
        "daily_tradability",
    ]


def test_historical_pipeline_audit_allows_cutoff_before_next_trading_day_availability(tmp_path):
    _write_configs(tmp_path)
    universe_path = tmp_path / "configs" / "universe_specs.json"
    universe_payload = json.loads(universe_path.read_text(encoding="utf-8"))
    universe_payload["universes"] = [
        spec
        for spec in universe_payload["universes"]
        if spec["universe_id"] == "tw_equity_liquid_top500"
    ]
    universe_path.write_text(json.dumps(universe_payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    run_pipeline(
        context,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )
    diagnostic = json.loads(
        context.store_path(
            "diagnostics", "historical_universe", "tw_equity_liquid_top500.json"
        ).read_text(encoding="utf-8")
    )
    diagnostic_path = context.store_path(
        "canonical",
        "derived",
        "universes",
        "tw_equity_liquid_top500",
        "diagnostics",
        "diagnostics.parquet",
    )
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([diagnostic]), diagnostic_path)

    audit = audit_store(context, config.artifact_contracts)
    verification = verify_runtime(context)

    assert audit["status"] == "ready"
    assert audit["metrics"]["unavailable_cutoff_count"] == 0
    assert verification["status"] == "ready"


def test_full_history_then_exact_daily_keeps_both_universe_manifests_active(tmp_path):
    _write_configs(tmp_path)
    universe_path = tmp_path / "configs" / "universe_specs.json"
    universe_payload = json.loads(universe_path.read_text(encoding="utf-8"))
    universe_payload["universes"] = [
        spec
        for spec in universe_payload["universes"]
        if spec["universe_id"] == "tw_equity_liquid_top500"
    ]
    universe_path.write_text(json.dumps(universe_payload), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    run_pipeline(
        context,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )
    historical_manifest_path = context.store_path(
        "manifests", "universe_tw_equity_liquid_top500.historical.json"
    )
    historical_before = json.loads(historical_manifest_path.read_text(encoding="utf-8"))
    legacy_payload = dict(historical_before)
    legacy_payload.pop("contract_key")
    legacy_payload.pop("variant")
    legacy_manifest_path = context.store_path(
        "manifests", "universe_tw_equity_liquid_top500.json"
    )
    legacy_manifest_path.write_text(
        json.dumps(legacy_payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    historical_manifest_path.unlink()

    run_pipeline(
        context,
        config,
        families={"financial_statement_raw"},
        as_of_date="2025-01-03",
        run_scope="daily",
    )
    exact_manifest_path = context.store_path(
        "manifests", "universe_tw_equity_liquid_top500.exact_date.json"
    )
    historical = json.loads(historical_manifest_path.read_text(encoding="utf-8"))
    exact = json.loads(exact_manifest_path.read_text(encoding="utf-8"))
    diagnostic = json.loads(
        context.store_path(
            "diagnostics", "historical_universe", "tw_equity_liquid_top500.json"
        ).read_text(encoding="utf-8")
    )
    diagnostic_path = context.store_path(
        "canonical", "derived", "universes", "tw_equity_liquid_top500",
        "diagnostics", "diagnostics.parquet",
    )
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([diagnostic]), diagnostic_path)

    audit = audit_store(context, config.artifact_contracts)
    verification = verify_runtime(context)
    inspection = inspect_artifacts(context)

    assert not legacy_manifest_path.exists()
    assert historical["artifact_paths"] == historical_before["artifact_paths"]
    assert historical["contract_key"] == "universe_tw_equity_liquid_top500:historical"
    assert exact["contract_key"] == "universe_tw_equity_liquid_top500:exact_date"
    assert all("membership_by_year" in path for path in historical["artifact_paths"])
    assert all("membership_by_date" in path for path in exact["artifact_paths"])
    assert set(audit["artifacts"]) >= {
        "universe_tw_equity_liquid_top500:historical",
        "universe_tw_equity_liquid_top500:exact_date",
    }
    assert audit["status"] == "ready"
    assert verification["status"] == "ready"
    assert {
        artifact["contract_key"]
        for artifact in inspection["artifacts"]
        if artifact["artifact_id"] == "universe_tw_equity_liquid_top500"
    } == {
        "universe_tw_equity_liquid_top500:historical",
        "universe_tw_equity_liquid_top500:exact_date",
    }


def test_full_history_run_migrates_live_like_legacy_historical_manifest(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    families = {"daily_price_volume", "security_master", "trading_calendar"}
    run_pipeline(
        context, config, families=families,
        run_scope="full_history",
    )
    contract = config.artifact_contracts[
        "universe_tw_equity_liquid_top500:historical"
    ]
    target = context.store_path("manifests", contract.manifest_file_name)
    original = json.loads(target.read_text(encoding="utf-8"))
    legacy_payload = dict(original)
    legacy_payload.pop("contract_key")
    legacy_payload.pop("variant")
    legacy = context.store_path(
        "manifests", "universe_tw_equity_liquid_top500.json"
    )
    legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")
    target.unlink()

    run_pipeline(
        context, config, families=families,
        run_scope="full_history",
    )

    migrated = json.loads(target.read_text(encoding="utf-8"))
    assert not legacy.exists()
    assert migrated["contract_key"] == contract.contract_key
    assert set(original["artifact_paths"]).isdisjoint(migrated["artifact_paths"])
    assert all(context.artifact_path(path).is_file() for path in original["artifact_paths"])


def test_inspect_reports_historical_universe_summary(tmp_path):
    _write_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    result = run_pipeline(
        context,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )

    assert result["status"] == "verifying"

    inspect_result = inspect_artifacts(context)
    assert inspect_result["historical_universe"]["status"] == "ready"
    assert inspect_result["historical_universe"]["small_file_daily_partition_count"] == 0
    assert inspect_result["historical_universe"]["historical_universe_count"] >= 1


def test_historical_publish_does_not_mutate_exact_snapshot_variant(tmp_path):
    _write_configs(tmp_path)
    stale_path = (
        tmp_path
        / "data_store"
        / "canonical"
        / "derived"
        / "universes"
        / "tw_equity_liquid_top500"
        / "membership_by_date"
        / "as_of_date=2025-12-31"
        / "membership.parquet"
    )
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist(
            [{
                "as_of_date": "2025-12-31",
                "universe_id": "tw_equity_liquid_top500",
                "ticker": "2330",
                "rank": 1,
                "data_cutoff_at": "2025-12-31T10:00:00Z",
            }]
        ),
        stale_path,
    )

    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    result = run_pipeline(
        context,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )

    assert result["status"] == "verifying"
    assert stale_path.exists()
    assert _active_path(
        context, "universe_tw_equity_liquid_top500.historical.json", "as_of_year=2025"
    ).exists()


def test_historical_publish_keeps_empty_universe_exact_snapshot_variant(tmp_path):
    _write_configs(tmp_path)
    stale_path = (
        tmp_path
        / "data_store"
        / "canonical"
        / "derived"
        / "universes"
        / "tpex_common_stock"
        / "membership_by_date"
        / "as_of_date=2025-12-31"
        / "membership.parquet"
    )
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist(
            [{
                "as_of_date": "2025-12-31",
                "universe_id": "tpex_common_stock",
                "ticker": "8069",
                "rank": 1,
                "data_cutoff_at": "2025-12-31T10:00:00Z",
            }]
        ),
        stale_path,
    )

    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    result = run_pipeline(
        context,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )

    assert result["status"] == "verifying"
    assert stale_path.exists()


def test_pipeline_publishes_historical_universe_with_raw_trading_calendar_shape(tmp_path):
    _write_configs(tmp_path, raw_historical_shapes=True)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    result = run_pipeline(
        context,
        config,
        families={"daily_price_volume", "security_master", "trading_calendar"},
        run_scope="full_history",
    )

    assert result["status"] == "verifying"

    security_panel_history_path = _active_path(
        context, "security_panel_history.json", "as_of_year=2025"
    )
    membership_path = _active_path(
        context, "universe_tw_equity_liquid_top500.historical.json", "as_of_year=2025"
    )
    assert security_panel_history_path.exists()
    assert membership_path.exists()

    inspect_result = inspect_artifacts(context)
    assert inspect_result["historical_universe"]["status"] == "ready"
    assert inspect_result["historical_universe"]["small_file_daily_partition_count"] == 0
