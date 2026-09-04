from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .model import oof_logistic_predictions
from .research import build_directional_training_frame, build_tier1_handoff
from .splits import chronological_purged_folds


@dataclass(frozen=True)
class Tier1OOFRun:
    training_frame: pd.DataFrame
    predictions: pd.DataFrame
    handoff: pd.DataFrame
    folds: list[tuple]


class Tier1Lab:
    """Read-only Notebook facade for reproducible Tier 1 OOF research."""

    def __init__(
        self,
        afml_root: Path,
        target_root: Path,
        feature_extension_root: Path | None = None,
    ) -> None:
        self.afml_root = afml_root
        self.target_root = target_root
        self.feature_extension_root = feature_extension_root

    @classmethod
    def from_artifacts(
        cls,
        afml_root: str | Path,
        target_root: str | Path,
        feature_extension_root: str | Path | None = None,
    ) -> "Tier1Lab":
        afml = Path(afml_root)
        targets = Path(target_root)
        if not (afml / "tables" / "features.parquet").is_file():
            raise ValueError("AFML artifact missing tables/features.parquet")
        if not (targets / "targets.parquet").is_file():
            raise ValueError("Tier 1 target artifact missing targets.parquet")
        extension = Path(feature_extension_root) if feature_extension_root is not None else None
        if extension is not None and not (extension / "features.parquet").is_file():
            raise ValueError("Tier 1 feature extension missing features.parquet")
        return cls(afml, targets, extension)

    def run_oof(
        self,
        feature_columns: list[str],
        outer_splits: int = 3,
        model_family: str = "logistic_regression",
        categorical_columns: tuple[str, ...] = (),
    ) -> Tier1OOFRun:
        targets = pd.read_parquet(self.target_root / "targets.parquet")
        features = pd.read_parquet(self.afml_root / "tables" / "features.parquet")
        if self.feature_extension_root is not None:
            extension = pd.read_parquet(self.feature_extension_root / "features.parquet")
            required = {"etf_id", "bar_id", "feature_available_at"}
            if missing := required.difference(extension.columns):
                raise ValueError(f"Tier 1 feature extension missing columns: {sorted(missing)}")
            if extension.duplicated(["etf_id", "bar_id"]).any():
                raise ValueError("Tier 1 feature extension has duplicate keys")
            base_clock = features[["etf_id", "bar_id", "feature_available_at"]]
            clock = base_clock.merge(
                extension[["etf_id", "bar_id", "feature_available_at"]],
                on=["etf_id", "bar_id"],
                how="inner",
                suffixes=("_base", "_extension"),
                validate="one_to_one",
            )
            if len(clock) != len(features):
                raise ValueError("Tier 1 feature extension does not cover every AFML feature key")
            base_time = pd.to_datetime(clock["feature_available_at_base"], errors="coerce", utc=True)
            extension_time = pd.to_datetime(clock["feature_available_at_extension"], errors="coerce", utc=True)
            if base_time.isna().any() or extension_time.isna().any() or not base_time.equals(extension_time):
                raise ValueError("Tier 1 feature extension availability must equal AFML feature availability")
            extension_columns = [
                column
                for column in extension.columns
                if column not in {"etf_id", "bar_id", "feature_available_at"}
            ]
            overlap = set(extension_columns).intersection(features.columns)
            if overlap:
                raise ValueError(f"Tier 1 feature extension overlaps AFML columns: {sorted(overlap)}")
            features = features.merge(
                extension[["etf_id", "bar_id", *extension_columns]],
                on=["etf_id", "bar_id"],
                how="inner",
                validate="one_to_one",
            )
        frame = build_directional_training_frame(targets, features, feature_columns)
        folds = chronological_purged_folds(frame[["t0", "t1"]], n_splits=outer_splits)
        predictions = oof_logistic_predictions(
            frame,
            [(train.tolist(), valid.tolist()) for train, valid in folds],
            feature_columns,
            model_family=model_family,
            categorical_columns=categorical_columns,
        )
        return Tier1OOFRun(training_frame=frame, predictions=predictions, handoff=build_tier1_handoff(frame, predictions), folds=folds)
