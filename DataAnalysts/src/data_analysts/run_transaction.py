from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from data_analysts.paths import DataAnalystsContext


_EXCLUDED_TOP_LEVEL = {"jobs", ".staging"}


class FormalStoreRollbackError(RuntimeError):
    def __init__(self, recovery_path: Path, failures: list[tuple[Path, Exception]]):
        self.recovery_path = recovery_path
        self.unrestored_targets = tuple(target.as_posix() for target, _ in failures)
        details = "; ".join(
            f"target={target}, error={error}" for target, error in failures
        )
        super().__init__(
            f"formal store rollback incomplete; recovery_path={recovery_path}; "
            f"unrestored_targets={list(self.unrestored_targets)!r}; {details}"
        )


class FormalStoreTransaction:
    """Run-level rollback for formal store files, excluding observable job state."""

    def __init__(self, context: DataAnalystsContext):
        self.context = context
        self._backup_root = Path(tempfile.mkdtemp(prefix="data-analysts-run-"))
        self._files: set[str] = set()
        self._directories: set[str] = set()
        self._committed = False
        try:
            self._snapshot()
        except Exception:
            shutil.rmtree(self._backup_root, ignore_errors=True)
            raise

    @property
    def recovery_path(self) -> str | None:
        return str(self._backup_root) if self._backup_root.exists() else None

    def __enter__(self) -> FormalStoreTransaction:
        return self

    def commit(self) -> None:
        self._committed = True

    def rollback(self) -> None:
        store = self.context.data_store
        failures: list[tuple[Path, Exception]] = []
        for path in sorted(_formal_files(store), reverse=True):
            relative = _relative(store, path)
            if relative not in self._files:
                try:
                    path.unlink()
                except OSError as exc:
                    failures.append((path, exc))
        for relative in sorted(self._files):
            backup = self._backup_root / Path(*PurePosixPath(relative).parts)
            target = store / Path(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.rollback")
            try:
                shutil.copyfile(backup, staging)
                os.replace(staging, target)
            except OSError as exc:
                failures.append((target, exc))
            finally:
                if staging.exists():
                    try:
                        staging.unlink()
                    except OSError:
                        pass
        for relative in sorted(self._directories, key=lambda value: value.count("/")):
            (store / Path(*PurePosixPath(relative).parts)).mkdir(parents=True, exist_ok=True)
        for path in sorted(_formal_directories(store), key=lambda item: len(item.parts), reverse=True):
            relative = _relative(store, path)
            if relative not in self._directories and path.exists():
                try:
                    path.rmdir()
                except OSError:
                    pass
        if failures:
            raise FormalStoreRollbackError(self._backup_root, failures)

    def close(self) -> None:
        shutil.rmtree(self._backup_root)

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is not None or not self._committed:
            self.rollback()
        self.close()
        return False

    def _snapshot(self) -> None:
        store = self.context.data_store
        if not store.exists():
            return
        for directory in _formal_directories(store):
            self._directories.add(_relative(store, directory))
        for source in _formal_files(store):
            relative = _relative(store, source)
            self._files.add(relative)
            backup = self._backup_root / Path(*PurePosixPath(relative).parts)
            backup.parent.mkdir(parents=True, exist_ok=True)
            try:
                if source.suffix.lower() != ".parquet":
                    raise OSError("small formal metadata uses an independent backup")
                os.link(source, backup)
            except OSError:
                shutil.copy2(source, backup)


def _formal_files(store: Path) -> list[Path]:
    if not store.exists():
        return []
    return [
        path
        for path in store.rglob("*")
        if path.is_file() and not _excluded(store, path)
    ]


def _formal_directories(store: Path) -> list[Path]:
    if not store.exists():
        return []
    return [
        path
        for path in store.rglob("*")
        if path.is_dir() and not _excluded(store, path)
    ]


def _excluded(store: Path, path: Path) -> bool:
    relative = path.relative_to(store)
    return bool(relative.parts and relative.parts[0] in _EXCLUDED_TOP_LEVEL)


def _relative(store: Path, path: Path) -> str:
    return PurePosixPath(*path.relative_to(store).parts).as_posix()
