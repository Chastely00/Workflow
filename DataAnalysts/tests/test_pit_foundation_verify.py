import json
import hashlib
from pathlib import Path
import shutil

import pytest

import data_analysts.filesystem as filesystem_module
from data_analysts.diagnostics import write_diagnostic
from data_analysts.config import load_runtime_config
from data_analysts.metadata import load_data_store_metadata, publish_data_store_metadata
from data_analysts.paths import DataAnalystsContext
from data_analysts.verify import verify_runtime


def copy_configs(src_root, dst_root):
    (dst_root / "configs").mkdir()
    for name in [
        "mongodb_sources.json",
        "source_family_profiles.json",
        "universe_specs.json",
        "source_catalog.json",
        "pit_registry.json",
        "artifact_contracts.json",
    ]:
        shutil.copy2(src_root / "configs" / name, dst_root / "configs" / name)


def publish_metadata_for_test(context: DataAnalystsContext) -> None:
    config = load_runtime_config(context)
    publish_data_store_metadata(context, config)


def _write_live_config(tmp_path: Path, name: str, payload: dict[str, object]) -> None:
    (tmp_path / "configs" / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _snapshot_dir(context: DataAnalystsContext) -> Path:
    metadata = load_data_store_metadata(context)
    snapshot_path = str(metadata["config_snapshot_path"]).replace("\\", "/")
    return context.data_store / Path(*snapshot_path.split("/"))


def _write_active_snapshot_payload(
    context: DataAnalystsContext,
    *,
    name: str,
    text: str,
) -> None:
    snapshot_file = _snapshot_dir(context) / name
    snapshot_file.write_text(text, encoding="utf-8")

    manifest_path = context.store_path("metadata", "data_store_manifest.json")
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata["config_hashes"][name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")


def test_write_diagnostic_stays_under_runtime_diagnostics(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)
    path = write_diagnostic(
        context,
        "pit_foundation/source_catalog",
        {"status": "ready", "forbidden_source_usage_count": 0},
    )

    assert (
        path
        == tmp_path
        / "data_store"
        / "diagnostics"
        / "pit_foundation"
        / "source_catalog.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["forbidden_source_usage_count"] == 0


def test_write_diagnostic_does_not_create_legacy_run_layout(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    path = write_diagnostic(
        context,
        "pit_foundation/source_catalog",
        {"status": "ready", "forbidden_source_usage_count": 0},
    )

    assert (
        path
        == tmp_path
        / "data_store"
        / "diagnostics"
        / "pit_foundation"
        / "source_catalog.json"
    )
    assert not (
        tmp_path
        / "runs"
        / "real_all_products"
        / "diagnostics"
        / "pit_foundation"
        / "source_catalog.json"
    ).exists()


def test_write_diagnostic_uses_temp_file_then_replace(tmp_path, monkeypatch):
    context = DataAnalystsContext.from_paths(tmp_path)
    writes: list[Path] = []
    replaces: list[tuple[Path, Path]] = []
    original_write_text = Path.write_text
    original_replace = filesystem_module.os.replace

    def recording_write_text(self: Path, data: str, *args, **kwargs):
        writes.append(self)
        return original_write_text(self, data, *args, **kwargs)

    def recording_replace(source: Path, target: Path):
        replaces.append((source, target))
        target.parent.mkdir(parents=True, exist_ok=True)
        original_replace(source, target)

    monkeypatch.setattr(filesystem_module.os, "replace", recording_replace)
    monkeypatch.setattr(Path, "write_text", recording_write_text)

    path = write_diagnostic(context, "pit_foundation/source_catalog", {"status": "ready"})

    assert writes == [path.with_name(f".{path.name}.tmp")]
    assert replaces == [(path.with_name(f".{path.name}.tmp"), path)]
    assert path.exists()


@pytest.mark.parametrize(
    "name",
    [
        r"C:\outside\x",
        "C:/outside/x",
        "/outside/x",
        "runtime/x",
        "nested/runs/x",
        "nested/real_all_products/x",
    ],
)
def test_write_diagnostic_rejects_absolute_like_names(tmp_path, name):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(ValueError):
        write_diagnostic(context, name, {"status": "blocked"})


def test_write_diagnostic_allows_substrings_that_are_not_forbidden_segments(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    path = write_diagnostic(
        context,
        "pit_foundation/company_runs_metric",
        {"status": "ready"},
    )

    assert path == (
        tmp_path
        / "data_store"
        / "diagnostics"
        / "pit_foundation"
        / "company_runs_metric.json"
    )


def test_verify_reports_pit_foundation_metrics(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    copy_configs(src_root, tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    publish_metadata_for_test(context)

    result = verify_runtime(context)

    assert "pit_foundation" in result
    metrics = result["pit_foundation"]
    assert metrics["forbidden_source_count"] == 2
    assert metrics["missing_pit_field_count"] == 0
    assert metrics["missing_logical_key_count"] == 0


def test_verify_blocks_missing_data_store_metadata(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    copy_configs(src_root, tmp_path)

    result = verify_runtime(DataAnalystsContext.from_paths(tmp_path))

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "metadata"


def test_verify_uses_snapshot_source_catalog_after_metadata_publish_despite_live_config_drift(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    copy_configs(src_root, tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    publish_metadata_for_test(context)

    payload = json.loads((tmp_path / "configs" / "source_catalog.json").read_text(encoding="utf-8"))
    payload["sources"][0]["pit_field"] = ""
    _write_live_config(tmp_path, "source_catalog.json", payload)

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "manifests"
    assert result["pit_foundation"]["missing_pit_field_count"] == 0


def test_verify_uses_snapshot_pit_registry_after_metadata_publish_despite_live_config_drift(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    copy_configs(src_root, tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    publish_metadata_for_test(context)

    payload = json.loads((tmp_path / "configs" / "pit_registry.json").read_text(encoding="utf-8"))
    first_family_id = next(iter(payload["families"]))
    payload["families"][first_family_id]["availability_field"] = ""
    _write_live_config(tmp_path, "pit_registry.json", payload)

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "manifests"
    assert result["pit_foundation"]["pit_registry_family_count"] > 0


def test_verify_blocks_metadata_when_active_snapshot_config_cannot_load(tmp_path):
    src_root = Path(__file__).resolve().parents[1]
    copy_configs(src_root, tmp_path)
    context = DataAnalystsContext.from_paths(tmp_path)
    publish_metadata_for_test(context)
    _write_active_snapshot_payload(
        context,
        name="source_catalog.json",
        text="{",
    )

    result = verify_runtime(context)

    assert result["status"] == "blocked"
    assert result["blocked_step"] == "metadata"
    assert "active config snapshot" in result["message"]
