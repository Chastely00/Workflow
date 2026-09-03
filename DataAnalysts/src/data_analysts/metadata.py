from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import uuid4

from data_analysts.config import (
    CONFIG_FILENAMES,
    RuntimeConfig,
    load_runtime_config_from_directory,
    load_runtime_config,
)
from data_analysts.filesystem import replace_file
from data_analysts.paths import DataAnalystsContext


REQUIRED_CONFIG_SNAPSHOT_FILES = list(CONFIG_FILENAMES)
LEGACY_ABSOLUTE_PATH_KEYS = ("data_store", "project_root_at_build_time")
PORTABLE_PATH_METADATA = {
    "path_reference": "DataAnalysts project root",
    "project_root": ".",
    "path_mode": "project_relative",
}
SUPPORTED_DATA_STORE_METADATA_SCHEMA_VERSION = "1.0"


def publish_data_store_metadata(
    context: DataAnalystsContext,
    config: RuntimeConfig,
) -> dict[str, object]:
    snapshot_dir, config_hashes = _publish_config_snapshot(context)

    data_store_root = _project_relative_path_or_none(context.project_root, context.data_store)
    metadata = {
        "schema_version": "1.0",
        "created_at": _utc_now(),
        "path_reference": "DataAnalysts project root",
        "project_root": ".",
        "data_store_root": data_store_root,
        "path_mode": "project_relative" if data_store_root is not None else "external_unrecorded",
        "config_snapshot_path": snapshot_dir,
        "config_hashes": config_hashes,
        "config_snapshot_file_count": len(config_hashes),
        "source_family_count": len(_list_payload_entries(config.source_family_profiles, "families")),
        "universe_spec_count": len(_list_payload_entries(config.universe_specs, "universes")),
    }
    _atomic_write_json(
        context.store_path("metadata", "data_store_manifest.json"),
        metadata,
    )
    return metadata


def load_data_store_metadata(
    context: DataAnalystsContext,
) -> dict[str, object]:
    path = context.store_path("metadata", "data_store_manifest.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("data_store manifest must be a JSON object")
    if payload.get("schema_version") != SUPPORTED_DATA_STORE_METADATA_SCHEMA_VERSION:
        raise ValueError("unsupported data_store manifest schema_version")
    return payload


def repair_data_store_metadata_paths(
    context: DataAnalystsContext,
) -> dict[str, object]:
    manifest_path = context.store_path("metadata", "data_store_manifest.json")
    metadata = load_data_store_metadata(context)
    if not isinstance(metadata, dict):
        raise ValueError("data_store manifest must be a JSON object")

    _verify_recorded_config_hashes(context, metadata)
    data_store_root = _project_relative_path_or_none(context.project_root, context.data_store)
    if data_store_root is None:
        raise ValueError("metadata_data_store_not_project_relative")

    repaired = dict(metadata)
    for key in LEGACY_ABSOLUTE_PATH_KEYS:
        repaired.pop(key, None)
    repaired.update(PORTABLE_PATH_METADATA)
    repaired["data_store_root"] = data_store_root

    if repaired != metadata:
        _atomic_write_json(manifest_path, repaired)
    return repaired


def load_active_snapshot_runtime_config(context: DataAnalystsContext) -> RuntimeConfig:
    metadata = load_data_store_metadata(context)
    snapshot_dir = _resolve_active_config_snapshot_dir(context, metadata)
    return load_runtime_config_from_directory(snapshot_dir)


def load_audit_runtime_config(
    context: DataAnalystsContext,
) -> tuple[RuntimeConfig, dict[str, object]]:
    """Load contracts for a read-only audit, including pre-registry stores.

    Pipeline execution intentionally continues to use the strict active snapshot
    loader.  Only audit may fall back to today's project registry so a damaged
    legacy store can be inventoried before migration.
    """
    metadata = load_data_store_metadata(context)
    snapshot_dir = _resolve_active_config_snapshot_dir(context, metadata)
    missing = [name for name in REQUIRED_CONFIG_SNAPSHOT_FILES if not (snapshot_dir / name).is_file()]
    if not missing:
        hash_metrics = verify_config_snapshot_hashes(context)
        return load_runtime_config_from_directory(snapshot_dir), {
            "mode": "active_snapshot",
            "active_snapshot_complete": True,
            "missing_files": [],
            "hash_status": (
                "verified"
                if hash_metrics["config_snapshot_hash_mismatch_count"] == 0
                else "mismatch"
            ),
            **hash_metrics,
            "active_snapshot_path": str(snapshot_dir.resolve()),
        }
    return load_runtime_config(context), {
        "mode": "project_registry_fallback",
        "active_snapshot_complete": False,
        "missing_files": sorted(missing),
        "hash_status": "legacy_incomplete",
        "config_snapshot_missing_count": len(missing),
        "config_snapshot_hash_mismatch_count": 0,
        "active_snapshot_path": str(snapshot_dir.resolve()),
    }


def verify_config_snapshot_hashes(
    context: DataAnalystsContext,
) -> dict[str, int]:
    metadata = load_data_store_metadata(context)
    expected_hashes = metadata.get("config_hashes")
    if not isinstance(expected_hashes, dict):
        raise ValueError("data_store manifest missing config_hashes")

    file_count = 0
    missing_count = 0
    mismatch_count = 0
    snapshot_dir = _resolve_active_config_snapshot_dir(context, metadata)
    for name in REQUIRED_CONFIG_SNAPSHOT_FILES:
        path = snapshot_dir / name
        expected = expected_hashes.get(name)
        if not path.exists():
            missing_count += 1
            continue
        file_count += 1
        payload = path.read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            mismatch_count += 1

    return {
        "config_snapshot_file_count": file_count,
        "config_snapshot_missing_count": missing_count,
        "config_snapshot_hash_mismatch_count": mismatch_count,
    }


def _verify_recorded_config_hashes(
    context: DataAnalystsContext,
    metadata: dict[str, object],
) -> None:
    expected_hashes = metadata.get("config_hashes")
    if not isinstance(expected_hashes, dict):
        raise ValueError("data_store manifest missing config_hashes")
    snapshot_dir = _resolve_active_config_snapshot_dir(context, metadata)
    for name in REQUIRED_CONFIG_SNAPSHOT_FILES:
        expected = expected_hashes.get(name)
        if not isinstance(expected, str) or not expected:
            raise ValueError(f"config_snapshot_hash_missing: {name}")
        snapshot_path = snapshot_dir / name
        if not snapshot_path.is_file():
            raise ValueError(f"config_snapshot_hash_missing: {name}")
        if hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"config_snapshot_hash_mismatch: {name}")

        live_path = context.config_path(name)
        if not live_path.is_file():
            raise ValueError(f"live_config_hash_missing: {name}")
        if hashlib.sha256(live_path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"live_config_hash_mismatch: {name}")


def _list_payload_entries(payload: dict[str, Any], key: str) -> list[object]:
    entries = payload.get(key)
    if not isinstance(entries, list):
        raise ValueError(f"runtime config missing list payload: {key}")
    return entries


def _publish_config_snapshot(
    context: DataAnalystsContext,
) -> tuple[str, dict[str, str]]:
    versioned_root = context.store_path("metadata", "config_snapshots")
    convenience_dir = context.store_path("metadata", "config_snapshot")
    versioned_root.mkdir(parents=True, exist_ok=True)
    snapshot_id = _next_snapshot_id()
    final_dir = versioned_root / snapshot_id
    if final_dir.exists():
        shutil.rmtree(final_dir)
    final_dir.mkdir()

    try:
        config_hashes: dict[str, str] = {}
        for name in REQUIRED_CONFIG_SNAPSHOT_FILES:
            payload = context.config_path(name).read_bytes()
            (final_dir / name).write_bytes(payload)
            config_hashes[name] = hashlib.sha256(payload).hexdigest()

        _publish_convenience_snapshot(final_dir, convenience_dir)
        snapshot_path = final_dir.relative_to(context.data_store).as_posix()
        return snapshot_path, config_hashes
    except Exception:
        if final_dir.exists():
            shutil.rmtree(final_dir)
        raise


def _publish_convenience_snapshot(source_dir: Path, target_dir: Path) -> None:
    stale_tmp_dir = target_dir.with_name(f".{target_dir.name}.tmp")
    if stale_tmp_dir.exists():
        shutil.rmtree(stale_tmp_dir)
    try:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
    except Exception:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        raise


def _resolve_active_config_snapshot_dir(
    context: DataAnalystsContext,
    metadata: dict[str, object],
) -> Path:
    configured_path = metadata.get("config_snapshot_path")
    if configured_path is None:
        return context.store_path("metadata", "config_snapshot")
    if not isinstance(configured_path, str):
        raise ValueError("data_store manifest config_snapshot_path must be a string")

    normalized_path = PurePosixPath(configured_path.replace("\\", "/"))
    return context.store_path(*normalized_path.parts)


def find_legacy_absolute_path_metadata(context: DataAnalystsContext) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    manifest_path = context.store_path("metadata", "data_store_manifest.json")
    if not manifest_path.exists():
        return findings
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return findings
    for key in sorted(LEGACY_ABSOLUTE_PATH_KEYS):
        value = payload.get(key)
        if isinstance(value, str) and _looks_like_absolute_machine_path(value):
            findings.append(
                {
                    "path": "metadata/data_store_manifest.json",
                    "key": key,
                    "reason": "legacy_absolute_path",
                }
            )
    return findings


def _looks_like_absolute_machine_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if bool(PureWindowsPath(value).drive):
        return True
    if PurePosixPath(normalized).is_absolute():
        return True
    return any(":" in part for part in PurePosixPath(normalized).parts)


def _project_relative_path_or_none(root: Path, path: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _next_snapshot_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"config-snapshot-{timestamp}-{uuid4().hex[:8]}"


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    staging = path.with_name(f".{path.name}.tmp")
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        replace_file(staging, path)
    finally:
        if staging.exists():
            staging.unlink()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
