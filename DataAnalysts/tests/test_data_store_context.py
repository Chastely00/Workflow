from pathlib import Path

import pytest

from data_analysts.paths import DataAnalystsContext, PathBoundaryError


def test_context_defaults_data_store_under_project_root(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    assert context.project_root == tmp_path.resolve()
    assert context.data_store == (tmp_path / "data_store").resolve()
    assert context.config_path("universe_specs.json") == (
        tmp_path / "configs" / "universe_specs.json"
    ).resolve()
    assert context.config_path("artifact_contracts.json") == (
        tmp_path / "configs" / "artifact_contracts.json"
    ).resolve()
    assert context.store_path("manifests") == (
        tmp_path / "data_store" / "manifests"
    ).resolve()


def test_context_accepts_external_data_store(tmp_path):
    store = tmp_path / "external_store"
    context = DataAnalystsContext.from_paths(tmp_path / "project", store)

    assert context.project_root == (tmp_path / "project").resolve()
    assert context.data_store == store.resolve()


def test_context_resolves_relative_data_store_against_project_root(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path / "project", "custom_store")

    assert context.project_root == (tmp_path / "project").resolve()
    assert context.data_store == (tmp_path / "project" / "custom_store").resolve()


def test_context_rejects_store_path_escape(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PathBoundaryError):
        context.artifact_path("../escape.parquet")


def test_context_rejects_absolute_artifact_path(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path(
            tmp_path / "data_store" / "canonical" / "x.parquet"
        )
    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path("/outside/x")


@pytest.mark.parametrize(
    "artifact_path",
    [
        "C:/store/file.parquet",
        "C:\\store\\file.parquet",
        "C:relative/file.parquet",
        "\\\\server\\share\\file.parquet",
        "//server/share/file.parquet",
        "canonical/raw/file.parquet:stream",
        "canonical\\raw/C:/inside.parquet",
    ],
)
def test_context_rejects_cross_platform_machine_artifact_paths(
    tmp_path, artifact_path
):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path(artifact_path)


def test_context_rejects_forbidden_path_segments(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path("runtime/canonical/x.parquet")
    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path("canonical/runs/x.parquet")
    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path("canonical/real_all_products/x.parquet")
    with pytest.raises(PathBoundaryError):
        context.validate_artifact_path("canonical/Runs/x.parquet")


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("config_path", ("../escape.json",)),
        ("contract_path", ("../escape.md",)),
        ("store_path", ("..", "escape")),
    ],
)
def test_context_rejects_boundary_traversal_for_formal_paths(
    tmp_path, method_name, args
):
    context = DataAnalystsContext.from_paths(tmp_path)

    with pytest.raises(PathBoundaryError):
        getattr(context, method_name)(*args)


def test_context_uses_path_segments_not_substrings(tmp_path):
    context = DataAnalystsContext.from_paths(tmp_path)

    assert context.validate_artifact_path(
        "canonical/raw/company_runs_metric/year=2025/part.parquet"
    ) == "canonical/raw/company_runs_metric/year=2025/part.parquet"
    assert context.validate_artifact_path(
        "canonical/raw/rolling_windows_20d/year=2025/part.parquet"
    ) == "canonical/raw/rolling_windows_20d/year=2025/part.parquet"


def test_legacy_layout_status_reports_without_blocking(tmp_path):
    (tmp_path / "runtime").mkdir()
    context = DataAnalystsContext.from_paths(tmp_path)

    assert context.legacy_layout_status() == {
        "legacy_project_runtime_exists": True,
        "legacy_project_runs_exists": False,
    }
