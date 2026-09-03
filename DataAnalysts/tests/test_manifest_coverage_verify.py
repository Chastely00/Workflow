from __future__ import annotations

import json
import shutil

import pyarrow as pa
import pyarrow.parquet as pq

from data_analysts.config import load_runtime_config
from data_analysts.dataset_publication import publish_dataset
from data_analysts.metadata import publish_data_store_metadata
from data_analysts.paths import DataAnalystsContext
from data_analysts.store_audit import audit_store
from data_analysts.verify import verify_runtime


def _context(tmp_path):
    source = __import__("pathlib").Path(__file__).parents[1] / "configs"
    configs = tmp_path / "configs"
    shutil.copytree(source, configs)
    profiles_path = configs / "source_family_profiles.json"
    profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
    for profile in profiles["families"]:
        profile["enabled"] = profile["family_id"] == "daily_price_volume"
    profiles_path.write_text(json.dumps(profiles), encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    publish_data_store_metadata(context, config)
    return context, config


def _row(date, ticker="2330"):
    return {
        "date": date, "ticker": ticker, "open": 100, "high": 101, "low": 99,
        "close": 100, "volume": 1000, "traded_value": 100000,
        "adj_factor": 1.0, "adj_close": 100.0,
        "data_cutoff_at": f"{date}T12:00:00Z",
    }


def test_verify_blocks_single_day_manifest_over_full_history_files(tmp_path):
    context, config = _context(tmp_path)
    contract = config.artifact_contracts["daily_price_volume"]
    publish_dataset(context, contract, [_row("2025-01-02"), _row("2026-07-08")], "full_history")
    manifest_path = context.store_path("manifests", "daily_price_volume.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_paths"] = [
        path for path in manifest["artifact_paths"] if "year=2026" in path
    ]
    manifest["row_count"] = 1
    manifest["date_range"] = ["2026-07-08", "2026-07-08"]
    manifest["availability_date_range"] = ["2026-07-08", "2026-07-08"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "artifact_inventory"
    assert result["metrics"]["orphan_partition_count"] == 1


def test_verify_blocks_manifest_parquet_row_count_mismatch(tmp_path):
    context, config = _context(tmp_path)
    contract = config.artifact_contracts["daily_price_volume"]
    publish_dataset(context, contract, [_row("2026-07-07"), _row("2026-07-08", "2317")], "full_history")
    manifest_path = context.store_path("manifests", "daily_price_volume.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["row_count"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "artifact_inventory"
    assert "row_count mismatch" in result["message"]


def test_verify_rejects_caller_full_history_scope_without_persisted_run_attestation(tmp_path):
    context, config = _context(tmp_path)
    contract = config.artifact_contracts["daily_price_volume"]
    publish_dataset(context, contract, [_row("2025-01-02"), _row("2026-07-08")], "full_history")
    before = audit_store(context, config.artifact_contracts)
    publish_dataset(context, contract, [_row("2026-07-08")], "full_history")

    full_result = verify_runtime(
        context, pre_publication_audit=before, run_scope="full_history"
    )
    assert full_result["status"] == "blocked"
    assert full_result["blocked_step"] == "run_attestation"


def test_prepublication_coverage_requires_explicit_run_scope(tmp_path):
    context, config = _context(tmp_path)
    contract = config.artifact_contracts["daily_price_volume"]
    publish_dataset(context, contract, [_row("2026-07-08")], "full_history")
    before = audit_store(context, config.artifact_contracts)

    result = verify_runtime(context, pre_publication_audit=before)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "run_attestation"
    assert "attestation" in result["message"]
