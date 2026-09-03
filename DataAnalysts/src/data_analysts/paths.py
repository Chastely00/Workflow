from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


FORBIDDEN_ARTIFACT_PATH_SEGMENTS = {"runtime", "runs", "real_all_products"}


class PathBoundaryError(ValueError):
    """Raised when a path would escape the DataAnalysts root."""


@dataclass(frozen=True)
class DataAnalystsContext:
    project_root: Path
    data_store: Path

    @classmethod
    def from_paths(
        cls,
        project_root: str | Path = ".",
        data_store: str | Path | None = None,
    ) -> "DataAnalystsContext":
        resolved_project_root = Path(project_root).expanduser().resolve()
        if data_store is None:
            resolved_data_store = (resolved_project_root / "data_store").resolve()
        else:
            store_candidate = Path(data_store).expanduser()
            if not store_candidate.is_absolute():
                store_candidate = resolved_project_root / store_candidate
            resolved_data_store = store_candidate.resolve()
        return cls(
            project_root=resolved_project_root,
            data_store=resolved_data_store,
        )

    def config_path(self, name: str) -> Path:
        return _resolve_under(self.project_root / "configs", name)

    def contract_path(self, name: str) -> Path:
        return _resolve_under(self.project_root / "contracts", name)

    def store_path(self, *parts: str) -> Path:
        return _resolve_under(self.data_store, *parts)

    def artifact_path(self, path: str | Path) -> Path:
        normalized = self.validate_artifact_path(path)
        parts = PurePosixPath(normalized).parts
        return self.store_path(*parts)

    def validate_artifact_path(self, path: str | Path) -> str:
        if is_absolute_artifact_path(path) or _has_windows_ads_or_embedded_drive(path):
            raise PathBoundaryError(f"artifact path must be relative: {path}")

        normalized = str(path).replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if ".." in parts:
            raise PathBoundaryError(f"artifact path cannot escape data_store: {path}")
        forbidden = FORBIDDEN_ARTIFACT_PATH_SEGMENTS.intersection(
            part.casefold() for part in parts
        )
        if forbidden:
            raise PathBoundaryError(
                f"artifact path contains forbidden segments: {sorted(forbidden)}"
            )
        return PurePosixPath(normalized).as_posix()

    def legacy_layout_status(self) -> dict[str, bool]:
        return {
            "legacy_project_runtime_exists": (self.project_root / "runtime").exists(),
            "legacy_project_runs_exists": (self.project_root / "runs").exists(),
        }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_under(root: Path, *parts: str | Path) -> Path:
    candidate = root.joinpath(*parts).resolve()
    if not _is_relative_to(candidate, root):
        raise PathBoundaryError(f"path resolves outside allowed root: {Path(*parts)}")
    return candidate


def is_absolute_artifact_path(path: str | Path) -> bool:
    raw = str(path)
    candidate = Path(raw)
    windows_candidate = PureWindowsPath(raw)
    if candidate.is_absolute() or bool(windows_candidate.drive):
        return True
    normalized = raw.replace("\\", "/")
    return PurePosixPath(normalized).is_absolute()


def _has_windows_ads_or_embedded_drive(path: str | Path) -> bool:
    normalized = str(path).replace("\\", "/")
    return any(":" in part for part in PurePosixPath(normalized).parts)
