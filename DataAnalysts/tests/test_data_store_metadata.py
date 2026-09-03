import hashlib
import json
from pathlib import Path

import pytest

import data_analysts.metadata as metadata_module
from data_analysts.config import load_runtime_config
from data_analysts.metadata import (
    find_legacy_absolute_path_metadata,
    load_data_store_metadata,
    publish_data_store_metadata,
    verify_config_snapshot_hashes,
    load_audit_runtime_config,
)
from data_analysts.paths import DataAnalystsContext


CONFIG_NAMES = [
    "mongodb_sources.json",
    "source_family_profiles.json",
    "universe_specs.json",
    "source_catalog.json",
    "pit_registry.json",
    "artifact_contracts.json",
]


def _copy_configs(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "configs"
    target = tmp_path / "configs"
    target.mkdir()
    for name in CONFIG_NAMES:
        (target / name).write_text(
            (source / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _resolve_store_relative_path(context: DataAnalystsContext, path: str) -> Path:
    return context.data_store / Path(path)


def test_publish_data_store_metadata_writes_manifest_and_config_snapshot(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    metadata = publish_data_store_metadata(context, config)

    manifest_path = tmp_path / "data_store" / "metadata" / "data_store_manifest.json"
    assert manifest_path.exists()
    snapshot_dir = tmp_path / "data_store" / "metadata" / "config_snapshot"
    assert sorted(path.name for path in snapshot_dir.glob("*.json")) == sorted(CONFIG_NAMES)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["config_snapshot_file_count"] == len(CONFIG_NAMES)
    assert sorted(payload["config_hashes"]) == sorted(CONFIG_NAMES)
    assert metadata["config_snapshot_file_count"] == len(CONFIG_NAMES)
    for name in CONFIG_NAMES:
        source_bytes = (tmp_path / "configs" / name).read_bytes()
        snapshot_bytes = (snapshot_dir / name).read_bytes()
        assert snapshot_bytes == source_bytes
        assert payload["config_hashes"][name] == hashlib.sha256(snapshot_bytes).hexdigest()


def test_publish_data_store_metadata_replaces_snapshot_directory(tmp_path):
    _copy_configs(tmp_path)
    snapshot_dir = tmp_path / "data_store" / "metadata" / "config_snapshot"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "stale.json").write_text('{"stale": true}', encoding="utf-8")
    temp_dir = snapshot_dir.with_name(f".{snapshot_dir.name}.tmp")
    temp_dir.mkdir(parents=True)
    (temp_dir / "leftover.json").write_text('{"leftover": true}', encoding="utf-8")
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    publish_data_store_metadata(context, config)

    assert sorted(path.name for path in snapshot_dir.iterdir()) == sorted(CONFIG_NAMES)
    assert not temp_dir.exists()


def test_find_legacy_absolute_path_metadata_detects_unc_network_paths(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    metadata_dir = tmp_path / "data_store" / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "data_store_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "project_root_at_build_time": ".",
                "data_store": "\\\\server\\share\\DataAnalysts\\data_store",
                "config_snapshot_path": "metadata/config_snapshot",
                "config_hashes": {},
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    findings = find_legacy_absolute_path_metadata(context)

    assert findings == [
        {
            "path": "metadata/data_store_manifest.json",
            "key": "data_store",
            "reason": "legacy_absolute_path",
        },
    ]


def test_load_data_store_metadata_reads_manifest_payload(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    published = publish_data_store_metadata(context, config)

    loaded = load_data_store_metadata(context)

    assert loaded == published


def test_load_data_store_metadata_rejects_unsupported_schema_version(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    publish_data_store_metadata(context, config)
    manifest_path = context.store_path("metadata", "data_store_manifest.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "2.0"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported data_store manifest schema_version"):
        load_data_store_metadata(context)


def test_verify_config_snapshot_hashes_detects_mismatch(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    metadata = publish_data_store_metadata(context, config)
    snapshot_dir = _resolve_store_relative_path(context, metadata["config_snapshot_path"])
    snapshot = snapshot_dir / "universe_specs.json"
    snapshot.write_text("{}", encoding="utf-8")

    result = verify_config_snapshot_hashes(context)

    assert result["config_snapshot_hash_mismatch_count"] == 1


def test_verify_config_snapshot_hashes_reports_existing_snapshot_file_count(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    metadata = publish_data_store_metadata(context, config)
    snapshot_dir = _resolve_store_relative_path(context, metadata["config_snapshot_path"])
    missing_snapshot = snapshot_dir / "pit_registry.json"
    missing_snapshot.unlink()

    result = verify_config_snapshot_hashes(context)

    assert result["config_snapshot_file_count"] == len(CONFIG_NAMES) - 1
    assert result["config_snapshot_missing_count"] == 1


def test_publish_data_store_metadata_keeps_previous_versioned_snapshot_and_updates_manifest_active_path(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    first_metadata = publish_data_store_metadata(context, config)
    first_snapshot_path = _resolve_store_relative_path(context, first_metadata["config_snapshot_path"])
    original_bytes = (first_snapshot_path / "universe_specs.json").read_bytes()
    updated_payload = json.loads((tmp_path / "configs" / "universe_specs.json").read_text(encoding="utf-8"))
    updated_payload["universes"][0]["enabled"] = False
    (tmp_path / "configs" / "universe_specs.json").write_text(
        json.dumps(updated_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    updated_config = load_runtime_config(context)

    second_metadata = publish_data_store_metadata(context, updated_config)

    second_snapshot_path = _resolve_store_relative_path(context, second_metadata["config_snapshot_path"])
    assert first_snapshot_path != second_snapshot_path
    assert first_snapshot_path.exists()
    assert second_snapshot_path.exists()
    assert (first_snapshot_path / "universe_specs.json").read_bytes() == original_bytes
    assert json.loads((second_snapshot_path / "universe_specs.json").read_text(encoding="utf-8")) == updated_payload

    loaded = load_data_store_metadata(context)
    assert loaded["config_snapshot_path"] == second_metadata["config_snapshot_path"]

    result = verify_config_snapshot_hashes(context)
    assert result["config_snapshot_missing_count"] == 0
    assert result["config_snapshot_hash_mismatch_count"] == 0


def test_verify_config_snapshot_hashes_uses_manifest_active_path_when_convenience_snapshot_missing(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    metadata = publish_data_store_metadata(context, config)

    convenience_snapshot_dir = tmp_path / "data_store" / "metadata" / "config_snapshot"
    for path in convenience_snapshot_dir.iterdir():
        path.unlink()
    convenience_snapshot_dir.rmdir()

    active_snapshot_dir = _resolve_store_relative_path(context, metadata["config_snapshot_path"])
    assert active_snapshot_dir.exists()

    result = verify_config_snapshot_hashes(context)

    assert result["config_snapshot_file_count"] == len(CONFIG_NAMES)
    assert result["config_snapshot_missing_count"] == 0
    assert result["config_snapshot_hash_mismatch_count"] == 0


def test_audit_config_falls_back_to_project_registry_for_legacy_snapshot(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    metadata = publish_data_store_metadata(context, config)
    snapshot_dir = _resolve_store_relative_path(context, metadata["config_snapshot_path"])
    (snapshot_dir / "artifact_contracts.json").unlink()

    loaded, evidence = load_audit_runtime_config(context)

    assert loaded.artifact_contracts
    assert evidence["mode"] == "project_registry_fallback"
    assert evidence["active_snapshot_complete"] is False
    assert "artifact_contracts.json" in evidence["missing_files"]
    assert evidence["hash_status"] == "legacy_incomplete"


def test_publish_data_store_metadata_succeeds_when_directory_rename_is_denied(tmp_path, monkeypatch):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    original_rename = Path.rename

    def deny_directory_rename(self: Path, target: Path):
        if self.is_dir():
            raise PermissionError("[WinError 5] Access is denied")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", deny_directory_rename)

    metadata = publish_data_store_metadata(context, config)

    versioned_snapshot_dir = _resolve_store_relative_path(context, metadata["config_snapshot_path"])
    convenience_snapshot_dir = tmp_path / "data_store" / "metadata" / "config_snapshot"
    assert versioned_snapshot_dir.exists()
    assert convenience_snapshot_dir.exists()
    assert sorted(path.name for path in versioned_snapshot_dir.glob("*.json")) == sorted(CONFIG_NAMES)
    assert sorted(path.name for path in convenience_snapshot_dir.glob("*.json")) == sorted(CONFIG_NAMES)

    result = verify_config_snapshot_hashes(context)

    assert result["config_snapshot_file_count"] == len(CONFIG_NAMES)
    assert result["config_snapshot_missing_count"] == 0
    assert result["config_snapshot_hash_mismatch_count"] == 0


def test_publish_data_store_metadata_uses_project_relative_paths(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)

    metadata = publish_data_store_metadata(context, config)

    assert metadata["path_reference"] == "DataAnalysts project root"
    assert metadata["project_root"] == "."
    assert metadata["data_store_root"] == "data_store"
    assert metadata["path_mode"] == "project_relative"
    assert "project_root_at_build_time" not in metadata
    assert "data_store" not in metadata
    assert "C:" not in json.dumps(metadata)


def test_publish_data_store_metadata_does_not_record_external_store_path(tmp_path):
    project_root = tmp_path / "project"
    external_store = tmp_path / "external_store"
    project_root.mkdir()
    _copy_configs(project_root)
    context = DataAnalystsContext.from_paths(project_root, external_store)
    config = load_runtime_config(context)

    metadata = publish_data_store_metadata(context, config)

    assert metadata["path_reference"] == "DataAnalysts project root"
    assert metadata["project_root"] == "."
    assert metadata["data_store_root"] is None
    assert metadata["path_mode"] == "external_unrecorded"
    assert str(external_store.resolve()) not in json.dumps(metadata)


def test_find_legacy_absolute_path_metadata_detects_old_manifest_fields(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    metadata_dir = tmp_path / "data_store" / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "data_store_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "project_root_at_build_time": "C:/Users/Old/DataAnalysts",
                "data_store": "C:/Users/Old/DataAnalysts/data_store",
                "config_snapshot_path": "metadata/config_snapshot",
                "config_hashes": {},
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    findings = find_legacy_absolute_path_metadata(context)

    assert findings == [
        {
            "path": "metadata/data_store_manifest.json",
            "key": "data_store",
            "reason": "legacy_absolute_path",
        },
        {
            "path": "metadata/data_store_manifest.json",
            "key": "project_root_at_build_time",
            "reason": "legacy_absolute_path",
        },
    ]


def test_find_legacy_absolute_path_metadata_detects_unc_network_path(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    metadata_dir = tmp_path / "data_store" / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "data_store_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "data_store": "\\\\server\\share\\DataAnalysts\\data_store",
                "config_snapshot_path": "metadata/config_snapshot",
                "config_hashes": {},
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    findings = find_legacy_absolute_path_metadata(context)

    assert findings == [
        {
            "path": "metadata/data_store_manifest.json",
            "key": "data_store",
            "reason": "legacy_absolute_path",
        },
    ]


@pytest.mark.parametrize(
    "legacy_path",
    [
        "/var/lib/DataAnalysts/data_store",
        "/opt/DataAnalysts/data_store",
        "C:relative\\DataAnalysts\\data_store",
        "\\\\server\\share\\DataAnalysts\\data_store",
        "metadata/config_snapshot.json:stream",
        "metadata\\config/C:/inside",
    ],
)
def test_find_legacy_absolute_path_metadata_matches_path_validator_grammar(
    tmp_path,
    legacy_path,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    metadata_dir = tmp_path / "data_store" / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "data_store_manifest.json").write_text(
        json.dumps({"data_store": legacy_path}),
        encoding="utf-8",
    )

    assert find_legacy_absolute_path_metadata(context) == [
        {
            "path": "metadata/data_store_manifest.json",
            "key": "data_store",
            "reason": "legacy_absolute_path",
        }
    ]


@pytest.mark.parametrize(
    "semantic_value",
    [
        "DataAnalysts project root",
        "rolling_windows_20d",
        "metadata/config_snapshot",
        "source_available_date_lte_decision_date",
    ],
)
def test_find_legacy_absolute_path_metadata_does_not_flag_semantic_strings(
    tmp_path,
    semantic_value,
):
    context = DataAnalystsContext.from_paths(tmp_path)
    metadata_dir = tmp_path / "data_store" / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "data_store_manifest.json").write_text(
        json.dumps({"data_store": semantic_value}),
        encoding="utf-8",
    )

    assert find_legacy_absolute_path_metadata(context) == []


def test_repair_data_store_metadata_paths_only_migrates_path_fields(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    config = load_runtime_config(context)
    published = publish_data_store_metadata(context, config)
    manifest_path = context.store_path("metadata", "data_store_manifest.json")
    legacy = {
        key: value
        for key, value in published.items()
        if key not in {"path_reference", "project_root", "data_store_root", "path_mode"}
    }
    legacy.update(
        {
            "project_root_at_build_time": "C:/Users/Old/DataAnalysts",
            "data_store": "C:/Users/Old/DataAnalysts/data_store",
            "active_snapshot_identity": "keep-this-lineage",
            "artifact_count": 17,
        }
    )
    manifest_path.write_text(json.dumps(legacy, indent=2, sort_keys=True), encoding="utf-8")
    artifact_manifest = context.store_path("manifests", "sentinel.json")
    artifact_manifest.parent.mkdir(parents=True)
    artifact_manifest.write_bytes(b'{"sentinel":true}')
    parquet = context.store_path("canonical", "sentinel.parquet")
    parquet.parent.mkdir(parents=True)
    parquet.write_bytes(b"parquet-sentinel")
    snapshot_dirs_before = sorted(
        path.name for path in context.store_path("metadata", "config_snapshots").iterdir()
    )

    repaired = metadata_module.repair_data_store_metadata_paths(context)

    assert repaired["created_at"] == published["created_at"]
    assert repaired["config_hashes"] == published["config_hashes"]
    assert repaired["config_snapshot_path"] == published["config_snapshot_path"]
    assert repaired["config_snapshot_file_count"] == published["config_snapshot_file_count"]
    assert repaired["source_family_count"] == published["source_family_count"]
    assert repaired["universe_spec_count"] == published["universe_spec_count"]
    assert repaired["active_snapshot_identity"] == "keep-this-lineage"
    assert repaired["artifact_count"] == 17
    assert repaired["path_reference"] == "DataAnalysts project root"
    assert repaired["project_root"] == "."
    assert repaired["data_store_root"] == "data_store"
    assert repaired["path_mode"] == "project_relative"
    assert "project_root_at_build_time" not in repaired
    assert "data_store" not in repaired
    assert artifact_manifest.read_bytes() == b'{"sentinel":true}'
    assert parquet.read_bytes() == b"parquet-sentinel"
    assert sorted(
        path.name for path in context.store_path("metadata", "config_snapshots").iterdir()
    ) == snapshot_dirs_before


def test_repair_data_store_metadata_paths_rejects_snapshot_hash_drift(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    metadata = publish_data_store_metadata(context, load_runtime_config(context))
    manifest_path = context.store_path("metadata", "data_store_manifest.json")
    manifest_before = manifest_path.read_bytes()
    snapshot_dir = _resolve_store_relative_path(context, metadata["config_snapshot_path"])
    (snapshot_dir / "universe_specs.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="config_snapshot_hash_mismatch"):
        metadata_module.repair_data_store_metadata_paths(context)

    assert manifest_path.read_bytes() == manifest_before


def test_repair_data_store_metadata_paths_rejects_live_config_drift(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    publish_data_store_metadata(context, load_runtime_config(context))
    manifest_path = context.store_path("metadata", "data_store_manifest.json")
    manifest_before = manifest_path.read_bytes()
    live_config = context.config_path("universe_specs.json")
    live_config.write_bytes(live_config.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="live_config_hash_mismatch"):
        metadata_module.repair_data_store_metadata_paths(context)

    assert manifest_path.read_bytes() == manifest_before


def test_repair_data_store_metadata_paths_is_byte_stable_for_portable_manifest(tmp_path):
    _copy_configs(tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    published = publish_data_store_metadata(context, load_runtime_config(context))
    manifest_path = context.store_path("metadata", "data_store_manifest.json")
    manifest_before = manifest_path.read_bytes()

    repaired = metadata_module.repair_data_store_metadata_paths(context)

    assert repaired == published
    assert manifest_path.read_bytes() == manifest_before


def test_metadata_repair_docs_define_path_only_migration_invariants():
    root = Path(__file__).resolve().parents[1]
    output_contract = (root / "contracts" / "OUTPUT_CONTRACT.md").read_text(encoding="utf-8")
    cli_contract = (root / "contracts" / "CLI_CONTRACT.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    for text in (output_contract, cli_contract, readme):
        assert "repair-metadata" in text
        assert "path-only migration" in text
        assert "created_at" in text
        assert "config hashes" in text
        assert "artifact manifests" in text
        assert "parquet" in text
