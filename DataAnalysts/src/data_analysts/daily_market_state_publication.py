"""Publication adapter for the ETF consumer's stricter DMS manifest contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pyarrow.parquet as pq

from data_analysts.artifacts import atomic_write_text
from data_analysts.config import RuntimeConfig
from data_analysts.dataset_publication import PublicationResult, publish_dataset
from data_analysts.paths import DataAnalystsContext


def publish_daily_market_state(
    context: DataAnalystsContext,
    config: RuntimeConfig,
    rows: list[dict[str, Any]],
    *,
    build_start: str,
    build_end: str,
    certified_source_start: str,
) -> PublicationResult:
    """Publish immutable partitions, then atomically add DMS authority evidence."""
    dependencies = _dependency_evidence(context)
    result = publish_dataset(
        context, config.artifact_contracts["daily_market_state"], rows, "bounded_backfill"
    )
    manifest = dict(result.manifest)
    inventory = [_inventory_item(context, path) for path in manifest["artifact_paths"]]
    schema_fingerprint = inventory[0]["schema_fingerprint"]
    if any(item["schema_fingerprint"] != schema_fingerprint for item in inventory):
        raise ValueError("daily_market_state partitions have incompatible physical schemas")
    manifest.update({
        "logical_key": ["date", "ticker"],
        "partition_inventory": inventory,
        "schema_fingerprint": schema_fingerprint,
        "dependency_manifest_sha256_by_contract": {
            key: value["sha256"] for key, value in dependencies.items()
        },
        "dependency_versions": {
            key: value["version"] for key, value in dependencies.items()
        },
        "dependency_certification_fingerprint": _sha256_json(dependencies),
        "build_start": build_start,
        "build_end": build_end,
        "certified_source_start": certified_source_start,
        "classification_policy_version": "daily_market_state_v3",
        "state_lattice_policy_version": "daily_market_state_lattice_v5",
        "market_identity_policy_version": "daily_market_identity_v3",
    })
    manifest_path = context.store_path("manifests", "daily_market_state.json")
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
    return PublicationResult(
        result.touched_paths, result.total_row_count, result.date_range, manifest_path, manifest,
        result.cleanup_diagnostics,
    )


def _dependency_evidence(context: DataAnalystsContext) -> dict[str, dict[str, str]]:
    expected = ("security_master", "trading_calendar", "daily_price_volume", "daily_tradability")
    result: dict[str, dict[str, str]] = {}
    for artifact_id in expected:
        path = context.store_path("manifests", f"{artifact_id}.json")
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("status") != "ready":
            raise ValueError(f"dependency manifest is not ready: {artifact_id}")
        version = payload.get("active_version")
        result[artifact_id] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "version": str(version) if isinstance(version, str) and version else "legacy-manifest",
        }
    return result


def _inventory_item(context: DataAnalystsContext, relative: str) -> dict[str, Any]:
    path = context.artifact_path(relative)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    parquet = pq.ParquetFile(path)
    try:
        row_count = parquet.metadata.num_rows
        schema_fingerprint = hashlib.sha256(
            parquet.schema_arrow.serialize().to_pybytes()
        ).hexdigest()
    finally:
        parquet.close()
    return {
        "path": relative, "content_sha256": digest, "size": path.stat().st_size,
        "row_count": row_count, "schema_fingerprint": schema_fingerprint,
    }


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
