from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from .config import AFMLContractError, AFMLScopeError


AFML_TABLE_NAMES = (
    "source_capabilities",
    "dollar_bars",
    "open_bar_checkpoints",
    "bar_daily_membership",
    "ffd_weights",
    "ffd_search",
    "ffd_series",
    "structural_features",
    "features",
    "events",
    "labels",
    "diagnostics",
)

_FORBIDDEN_FEATURE_COLUMNS = {
    "label",
    "t1",
    "label_available_at",
    "first_touch_date",
    "first_touch_type",
    "realized_log_return",
}

_DEFAULT_KEYS: dict[str, tuple[str, ...]] = {
    "source_capabilities": ("feature_id",),
    "dollar_bars": ("etf_id", "bar_id"),
    "open_bar_checkpoints": ("etf_id", "bar_id"),
    "bar_daily_membership": ("etf_id", "bar_id", "date"),
    "ffd_weights": ("etf_id", "calibration_version", "weight_lag"),
    "ffd_search": ("etf_id", "search_order"),
    "ffd_series": ("etf_id", "bar_id"),
    "structural_features": ("entity_id", "observation_id"),
    "features": ("etf_id", "bar_id"),
    "events": ("event_id",),
    "labels": ("event_id",),
    "diagnostics": (),
}


@dataclass(frozen=True)
class AFMLDataset:
    source_capabilities: pd.DataFrame
    dollar_bars: pd.DataFrame
    open_bar_checkpoints: pd.DataFrame
    bar_daily_membership: pd.DataFrame
    ffd_weights: pd.DataFrame
    ffd_search: pd.DataFrame
    ffd_series: pd.DataFrame
    structural_features: pd.DataFrame
    features: pd.DataFrame
    events: pd.DataFrame
    labels: pd.DataFrame
    diagnostics: pd.DataFrame
    metadata: dict[str, Any]
    readiness: dict[str, Any]

    SCHEMA_VERSION: ClassVar[str] = "etf-afml-dataset-v1"

    def __post_init__(self) -> None:
        for name in AFML_TABLE_NAMES:
            frame = getattr(self, name)
            if not isinstance(frame, pd.DataFrame):
                raise AFMLContractError(f"{name} must be a pandas DataFrame")
            _validate_table(name, frame)
        leaked = _FORBIDDEN_FEATURE_COLUMNS.intersection(self.features.columns)
        if leaked:
            raise AFMLContractError(
                f"features contains future label columns: {sorted(leaked)}"
            )
        if not isinstance(self.metadata, dict) or not isinstance(self.readiness, dict):
            raise AFMLContractError("metadata and readiness must be dictionaries")
        if self.metadata.get("schema_version") != self.SCHEMA_VERSION:
            raise AFMLContractError(
                f"metadata schema_version must equal {self.SCHEMA_VERSION!r}"
            )
        if self.metadata.get("config_sha256") in (None, ""):
            raise AFMLContractError("metadata config_sha256 is required")

    @property
    def train(self) -> pd.DataFrame:
        return self._split_view("train")

    @property
    def validation(self) -> pd.DataFrame:
        return self._split_view("validation")

    @property
    def test(self) -> pd.DataFrame:
        return self._split_view("test")

    def for_ml(self, etf_id: str, split: str = "train") -> pd.DataFrame:
        if self.metadata.get("readiness_scope") == "DESCRIPTIVE_ONLY":
            raise AFMLScopeError(
                "research_full_history dataset is DESCRIPTIVE_ONLY and cannot feed ML"
            )
        if split not in {"train", "validation", "test"}:
            raise AFMLContractError(
                "split must be one of 'train', 'validation', or 'test'"
            )
        expected_ids = {str(value) for value in self.metadata.get("etf_ids", ())}
        if expected_ids and etf_id not in expected_ids:
            raise KeyError(f"unknown ETF: {etf_id}")
        return self._split_view(split).loc[lambda x: x["etf_id"].eq(etf_id)].reset_index(
            drop=True
        )

    def for_trading(
        self, as_of: str | pd.Timestamp, decision_cutoff: str = "after_close"
    ) -> pd.DataFrame:
        """Return PIT-safe feature snapshots without touching label/event tables."""

        decision_time = _decision_time(as_of, decision_cutoff)
        expected_ids = tuple(str(value) for value in self.metadata.get("etf_ids", ()))
        if not expected_ids:
            expected_ids = tuple(
                self.features["etf_id"].dropna().astype(str).drop_duplicates()
            )
        gate_columns = [
            column
            for column in (
                "etf_id",
                "bar_id",
                "bar_status",
                "bar_role",
                "live_eligible",
                "calibration_effective_at",
                "bar_available_at",
                "source_quality_flag",
            )
            if column in self.dollar_bars.columns
        ]
        bars = self.dollar_bars[gate_columns].rename(
            columns={"source_quality_flag": "bar_source_quality_flag"}
        )
        candidates = self.features.merge(
            bars, on=["etf_id", "bar_id"], how="left", validate="one_to_one"
        )
        candidates["feature_available_at"] = pd.to_datetime(
            candidates["feature_available_at"], errors="coerce"
        )
        if "calibration_effective_at" in candidates:
            candidates["calibration_effective_at"] = pd.to_datetime(
                candidates["calibration_effective_at"], errors="coerce"
            )
        candidates = candidates[candidates["feature_available_at"].le(decision_time)]

        rows: list[dict[str, object]] = []
        for etf_id in expected_ids:
            all_bars = self.dollar_bars[self.dollar_bars["etf_id"].eq(etf_id)]
            effective = pd.to_datetime(
                all_bars.get("calibration_effective_at"), errors="coerce"
            ).dropna()
            if not effective.empty and decision_time < effective.min():
                rows.append(
                    _unavailable_trading_row(
                        etf_id, decision_time, "PRE_CALIBRATION"
                    )
                )
                continue
            available = candidates[candidates["etf_id"].eq(etf_id)].copy()
            if "bar_status" in available:
                available = available[available["bar_status"].eq("FINALIZED")]
            if "live_eligible" in available:
                available = available[available["live_eligible"].eq(True)]
            if "calibration_effective_at" in available:
                available = available[
                    available["calibration_effective_at"].isna()
                    | available["calibration_effective_at"].le(decision_time)
                ]
            if "ffd_missing" in available:
                available = available[~available["ffd_missing"].fillna(True)]
            elif "ffd_level" in available:
                available = available[available["ffd_level"].notna()]
            if available.empty:
                provisional = self.open_bar_checkpoints[
                    self.open_bar_checkpoints.get(
                        "etf_id", pd.Series(dtype=object)
                    ).eq(etf_id)
                ]
                status = (
                    "OPEN_PROVISIONAL_ONLY"
                    if not provisional.empty
                    else "NO_FEATURE_READY_BAR"
                )
                rows.append(_unavailable_trading_row(etf_id, decision_time, status))
                continue
            selected = available.sort_values(
                ["feature_available_at", "bar_id"], kind="stable"
            ).iloc[-1]
            quality_failed = bool(selected.get("source_quality_flag", False)) or bool(
                selected.get("bar_source_quality_flag", False)
            )
            alignment_reason = selected.get("ix_alignment_reason")
            if quality_failed:
                rows.append(
                    _unavailable_trading_row(
                        etf_id, decision_time, "SOURCE_QUALITY_FAILED"
                    )
                )
                continue
            if pd.notna(alignment_reason):
                rows.append(
                    _unavailable_trading_row(
                        etf_id, decision_time, str(alignment_reason)
                    )
                )
                continue
            row = selected.to_dict()
            row["decision_time"] = decision_time
            execution = _next_session(
                self.metadata.get("trading_sessions", ()), decision_time
            )
            row["earliest_execution_session"] = execution
            row["availability_status"] = (
                "AVAILABLE" if pd.notna(execution) else "NO_FUTURE_EXECUTION_SESSION"
            )
            rows.append(row)
        result = pd.DataFrame(rows)
        forbidden = _FORBIDDEN_FEATURE_COLUMNS.intersection(result.columns)
        if forbidden:
            raise AFMLContractError(
                f"trading snapshot exposed forbidden label columns: {sorted(forbidden)}"
            )
        return result

    def write(self, output_dir: str | Path) -> dict[str, Any]:
        destination = Path(output_dir).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"AFML output already exists: {destination}")
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
        )
        try:
            table_evidence: dict[str, dict[str, Any]] = {}
            tables_dir = temporary / "tables"
            tables_dir.mkdir()
            for name in AFML_TABLE_NAMES:
                frame = getattr(self, name)
                relative = Path("tables") / f"{name}.parquet"
                path = temporary / relative
                frame.to_parquet(path, index=False)
                table_evidence[name] = {
                    "path": relative.as_posix(),
                    "sha256": _file_sha256(path),
                    "row_count": len(frame),
                    "columns": frame.columns.tolist(),
                    "dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()},
                    "key": list(_table_key(name, frame)),
                }
            metadata_path = temporary / "metadata.json"
            readiness_path = temporary / "readiness.json"
            _write_json(metadata_path, self.metadata)
            _write_json(readiness_path, self.readiness)
            manifest: dict[str, Any] = {
                "schema_version": self.SCHEMA_VERSION,
                "tables": table_evidence,
                "metadata_path": metadata_path.name,
                "metadata_sha256": _file_sha256(metadata_path),
                "readiness_path": readiness_path.name,
                "readiness_sha256": _file_sha256(readiness_path),
            }
            _write_json(temporary / "manifest.json", manifest)
            os.replace(temporary, destination)
            return manifest
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    @classmethod
    def read(cls, output_dir: str | Path) -> "AFMLDataset":
        root = Path(output_dir).resolve()
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise AFMLContractError(f"AFML manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != cls.SCHEMA_VERSION:
            raise AFMLContractError("AFML manifest schema version mismatch")
        table_entries = manifest.get("tables")
        if not isinstance(table_entries, dict) or set(table_entries) != set(
            AFML_TABLE_NAMES
        ):
            raise AFMLContractError("AFML manifest canonical table set mismatch")
        tables: dict[str, pd.DataFrame] = {}
        for name in AFML_TABLE_NAMES:
            evidence = table_entries[name]
            path = root / evidence["path"]
            if _file_sha256(path) != evidence["sha256"]:
                raise AFMLContractError(f"SHA-256 mismatch for table {name}")
            frame = pd.read_parquet(path)
            if len(frame) != evidence["row_count"]:
                raise AFMLContractError(f"row count mismatch for table {name}")
            if frame.columns.tolist() != evidence["columns"]:
                raise AFMLContractError(f"column schema mismatch for table {name}")
            tables[name] = frame
        metadata_path = root / manifest["metadata_path"]
        readiness_path = root / manifest["readiness_path"]
        if _file_sha256(metadata_path) != manifest["metadata_sha256"]:
            raise AFMLContractError("SHA-256 mismatch for metadata")
        if _file_sha256(readiness_path) != manifest["readiness_sha256"]:
            raise AFMLContractError("SHA-256 mismatch for readiness")
        return cls(
            **tables,
            metadata=json.loads(metadata_path.read_text(encoding="utf-8")),
            readiness=json.loads(readiness_path.read_text(encoding="utf-8")),
        )

    def _split_view(self, split: str) -> pd.DataFrame:
        cutoffs = {
            "train": (
                None,
                pd.Timestamp(self.metadata["train_decision_cutoff"]),
            ),
            "validation": (
                pd.Timestamp(self.metadata["train_decision_cutoff"]),
                pd.Timestamp(self.metadata["validation_decision_cutoff"]),
            ),
            "test": (
                pd.Timestamp(self.metadata["validation_decision_cutoff"]),
                pd.Timestamp(self.metadata["test_decision_cutoff"]),
            ),
        }
        if split not in cutoffs:
            raise AFMLContractError(f"unknown split: {split}")
        lower, upper = cutoffs[split]
        frame = self.features.copy()
        available = pd.to_datetime(frame["feature_available_at"], errors="coerce")
        mask = available.le(upper)
        if lower is not None:
            mask &= available.gt(lower)
        frame = frame[mask].copy()

        event_columns = [
            column
            for column in (
                "etf_id",
                "event_id",
                "t0_bar_id",
                "t0_observation_date",
                "event_available_at",
                "event_concurrency_at_t0",
                "max_event_concurrency",
                "average_uniqueness",
            )
            if column in self.events.columns
        ]
        events = self.events[event_columns].rename(columns={"t0_bar_id": "bar_id"})
        frame = frame.merge(
            events,
            on=["etf_id", "bar_id"],
            how="left",
            validate="one_to_one",
        )
        label_columns = [
            column
            for column in (
                "event_id",
                "t1",
                "label_available_at",
                "first_touch_date",
                "first_touch_type",
                "realized_log_return",
                "label",
                "label_status",
                f"eligible_for_{split}",
            )
            if column in self.labels.columns
        ]
        frame = frame.merge(
            self.labels[label_columns],
            on="event_id",
            how="left",
            validate="one_to_one",
        )
        eligibility_column = f"eligible_for_{split}"
        if eligibility_column in frame:
            eligible = frame[eligibility_column].fillna(False)
        else:
            eligible = pd.Series(False, index=frame.index)
        outcome_columns = [
            column
            for column in (
                "t1",
                "label_available_at",
                "first_touch_date",
                "first_touch_type",
                "realized_log_return",
                "label",
            )
            if column in frame.columns
        ]
        frame.loc[~eligible, outcome_columns] = np.nan
        frame["label_join_status"] = np.where(
            eligible,
            "ELIGIBLE",
            np.where(frame["event_id"].isna(), "NO_EVENT", "CUTOFF_INELIGIBLE"),
        )
        return frame.sort_values(["etf_id", "bar_id"], kind="stable").reset_index(
            drop=True
        )


def _table_key(name: str, frame: pd.DataFrame) -> tuple[str, ...]:
    if name == "ffd_search":
        if "search_order" in frame.columns:
            candidates = ("etf_id", "q_calibration_version", "search_order")
        else:
            candidates = (
                "etf_id",
                "q_calibration_version",
                "calibration_version",
                "phase",
                "d",
            )
        return tuple(column for column in candidates if column in frame.columns)
    if name == "structural_features" and not {
        "entity_id",
        "observation_id",
    }.issubset(frame.columns):
        candidates = ("entity_id", "etf_id", "bar_id", "date")
        return tuple(column for column in candidates if column in frame.columns)
    return _DEFAULT_KEYS[name]


def _validate_table(name: str, frame: pd.DataFrame) -> None:
    key = _table_key(name, frame)
    missing = sorted(set(key).difference(frame.columns))
    if missing and not frame.empty:
        raise AFMLContractError(f"{name} missing key columns: {missing}")
    if key and not missing and frame.duplicated(list(key)).any():
        raise AFMLContractError(f"{name} has duplicate canonical key {key}")


def _decision_time(value: str | pd.Timestamp, decision_cutoff: str) -> pd.Timestamp:
    if decision_cutoff != "after_close":
        raise AFMLContractError("decision_cutoff must equal 'after_close'")
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Asia/Taipei")
    else:
        timestamp = timestamp.tz_convert("Asia/Taipei")
    if timestamp == timestamp.normalize():
        timestamp = timestamp.normalize() + pd.Timedelta(days=1) - pd.Timedelta(
            nanoseconds=1
        )
    return timestamp


def _next_session(values: object, decision_time: pd.Timestamp) -> pd.Timestamp | pd.NaT:
    if not isinstance(values, (list, tuple)):
        return pd.NaT
    sessions = pd.DatetimeIndex(pd.to_datetime(list(values), errors="coerce")).dropna()
    decision_date = decision_time.tz_localize(None).normalize()
    future = sessions[sessions > decision_date]
    return pd.Timestamp(future.min()).normalize() if len(future) else pd.NaT


def _unavailable_trading_row(
    etf_id: str, decision_time: pd.Timestamp, status: str
) -> dict[str, object]:
    return {
        "etf_id": etf_id,
        "bar_id": np.nan,
        "feature_available_at": pd.NaT,
        "decision_time": decision_time,
        "earliest_execution_session": pd.NaT,
        "live_eligible": False,
        "availability_status": status,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(
            _jsonable(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if value is pd.NaT or value is pd.NA:
        return None
    return value
