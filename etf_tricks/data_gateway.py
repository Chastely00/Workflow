from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd


class DataContractError(RuntimeError):
    pass


_DATE_COLUMNS = {
    "date",
    "source_available_date",
    "source_period_date",
    "period_start_date",
    "period_end_date",
    "revision_date",
    "list_date",
    "delist_date",
    "event_date",
    "ex_date",
}


class DataGateway:
    def __init__(self, data_analysts_root: Path) -> None:
        self.data_analysts_root = data_analysts_root.resolve()
        self.data_store = (self.data_analysts_root / "data_store").resolve()
        self.manifest_dir = self.data_store / "manifests"

    @classmethod
    def from_data_analysts(cls, root: str | Path) -> "DataGateway":
        return cls(Path(root))

    def load_manifest(self, artifact_id: str) -> dict[str, object]:
        path = self.manifest_dir / f"{artifact_id}.json"
        if not path.is_file():
            raise DataContractError(f"missing manifest for artifact {artifact_id}: {path}")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataContractError(f"invalid manifest for artifact {artifact_id}: {exc}") from exc
        if manifest.get("artifact_id") != artifact_id:
            raise DataContractError(
                f"manifest artifact mismatch for {artifact_id}: {manifest.get('artifact_id')}"
            )
        status = manifest.get("status")
        if status != "ready":
            raise DataContractError(f"artifact {artifact_id} status is {status}, expected ready")
        return manifest

    def read_artifact(
        self,
        artifact_id: str,
        *,
        columns: Iterable[str] | None = None,
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
        date_column: str | None = None,
    ) -> pd.DataFrame:
        manifest = self.load_manifest(artifact_id)
        declared_columns = tuple(str(value) for value in manifest.get("columns", ()))
        selected_columns = tuple(columns) if columns is not None else declared_columns
        missing = sorted(set(selected_columns).difference(declared_columns))
        if missing:
            raise DataContractError(
                f"artifact {artifact_id} manifest missing requested columns: {missing}"
            )
        self._validate_requested_coverage(artifact_id, manifest, start, end)

        frames: list[pd.DataFrame] = []
        raw_paths = manifest.get("artifact_paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise DataContractError(f"artifact {artifact_id} has no declared artifact_paths")
        for raw_path in raw_paths:
            path = (self.data_store / str(raw_path)).resolve()
            if not path.is_relative_to(self.data_store):
                raise DataContractError(
                    f"artifact {artifact_id} path is outside data_store: {raw_path}"
                )
            if not path.is_file():
                raise DataContractError(f"artifact {artifact_id} declared file is missing: {path}")
            try:
                frames.append(pd.read_parquet(path, columns=list(selected_columns)))
            except Exception as exc:
                raise DataContractError(
                    f"failed reading artifact {artifact_id} file {path}: {exc}"
                ) from exc

        result = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        for column in selected_columns:
            if column in _DATE_COLUMNS:
                result[column] = pd.to_datetime(result[column], errors="coerce")

        effective_date_column = date_column
        if effective_date_column is None:
            if "date" in result.columns:
                effective_date_column = "date"
            elif "source_available_date" in result.columns:
                effective_date_column = "source_available_date"
        if start is not None or end is not None:
            if effective_date_column is None or effective_date_column not in result.columns:
                raise DataContractError(
                    f"artifact {artifact_id} has no date column for requested filtering"
                )
            if start is not None:
                result = result[result[effective_date_column] >= pd.Timestamp(start)]
            if end is not None:
                result = result[result[effective_date_column] <= pd.Timestamp(end)]
        return result.reset_index(drop=True)

    @staticmethod
    def _validate_requested_coverage(
        artifact_id: str,
        manifest: dict[str, object],
        start: str | pd.Timestamp | None,
        end: str | pd.Timestamp | None,
    ) -> None:
        if start is None and end is None:
            return
        raw_range = manifest.get("date_range") or manifest.get("availability_date_range")
        if not isinstance(raw_range, list) or len(raw_range) != 2:
            return
        lower, upper = pd.Timestamp(raw_range[0]), pd.Timestamp(raw_range[1])
        if start is not None and pd.Timestamp(start) < lower:
            raise DataContractError(
                f"artifact {artifact_id} coverage starts at {lower.date()}, requested {start}"
            )
        if end is not None and pd.Timestamp(end) > upper:
            raise DataContractError(
                f"artifact {artifact_id} coverage ends at {upper.date()}, requested {end}"
            )

