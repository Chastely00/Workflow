from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd

from .model import oof_logistic_predictions
from .research import build_directional_training_frame, build_tier1_handoff
from .splits import chronological_purged_folds
from .long_history import validate_fold_feature_coverage


@dataclass(frozen=True)
class Tier1OOFRun:
    training_frame: pd.DataFrame
    predictions: pd.DataFrame
    handoff: pd.DataFrame
    folds: list[tuple]


@dataclass(frozen=True)
class Tier1LocalOOFRuns:
    """ETF-local OOF runs; no fitted state or training rows cross ETF boundaries."""

    by_etf: dict[str, Tier1OOFRun]


class Tier1Lab:
    """Read-only Notebook facade for reproducible Tier 1 OOF research."""

    def __init__(
        self,
        afml_root: Path,
        target_root: Path,
        feature_extension_root: Path | None = None,
        trading_sessions: pd.DatetimeIndex | None = None,
    ) -> None:
        self.afml_root = afml_root
        self.target_root = target_root
        self.feature_extension_root = feature_extension_root
        self.trading_sessions = trading_sessions

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
        metadata_path = afml / "metadata.json"
        if not metadata_path.is_file():
            raise ValueError("AFML artifact missing metadata.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        sessions = pd.DatetimeIndex(pd.to_datetime(metadata.get("trading_sessions"), errors="coerce"))
        if sessions.empty or sessions.isna().any() or sessions.has_duplicates:
            raise ValueError("AFML metadata requires valid unique trading_sessions")
        return cls(afml, targets, extension, sessions.sort_values())

    def run_oof(
        self,
        feature_columns: list[str],
        outer_splits: int = 3,
        model_family: str = "logistic_regression",
        categorical_columns: tuple[str, ...] = (),
        candidate_threshold_objective: str = "f1",
        minimum_candidate_weight_share: float = 0.10,
        research_t0_end: str | pd.Timestamp | None = None,
        research_outcome_before: str | pd.Timestamp | None = None,
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
        if (research_t0_end is None) != (research_outcome_before is None):
            raise ValueError(
                "research_t0_end and research_outcome_before must be supplied together"
            )
        if research_t0_end is not None and research_outcome_before is not None:
            t0_end = pd.Timestamp(research_t0_end).normalize()
            outcome_before = pd.Timestamp(research_outcome_before).normalize()
            if t0_end >= outcome_before:
                raise ValueError("research t0 end must precede sealed outcome boundary")
            frame = frame.loc[
                frame["t0"].le(t0_end) & frame["t1"].lt(outcome_before)
            ].reset_index(drop=True)
            if frame.empty:
                raise ValueError("research cut-off excludes every resolved target")
        folds = chronological_purged_folds(frame[["t0", "t1"]], n_splits=outer_splits)
        validate_fold_feature_coverage(
            frame,
            [(train.tolist(), valid.tolist()) for train, valid in folds],
            feature_columns,
        )
        predictions = oof_logistic_predictions(
            frame,
            [(train.tolist(), valid.tolist()) for train, valid in folds],
            feature_columns,
            model_family=model_family,
            categorical_columns=categorical_columns,
            candidate_threshold_objective=candidate_threshold_objective,
            minimum_candidate_weight_share=minimum_candidate_weight_share,
            trading_sessions=self.trading_sessions,
            uniqueness_entity_column="etf_id",
        )
        return Tier1OOFRun(training_frame=frame, predictions=predictions, handoff=build_tier1_handoff(frame, predictions), folds=folds)

    def run_oof_per_etf(
        self,
        feature_columns: list[str],
        outer_splits: int = 3,
        model_family: str = "logistic_regression",
        categorical_columns: tuple[str, ...] = (),
        candidate_threshold_objective: str = "f1",
        minimum_candidate_weight_share: float = 0.10,
        research_t0_end: str | pd.Timestamp | None = None,
        research_outcome_before: str | pd.Timestamp | None = None,
    ) -> Tier1LocalOOFRuns:
        """Fit one OOF model per ETF; ``etf_id`` is lineage metadata, never a feature."""
        if "etf_id" in categorical_columns:
            raise ValueError("ETF-local OOF must not use etf_id as a categorical feature")
        frame = self._build_training_frame(
            feature_columns, research_t0_end, research_outcome_before
        )
        runs: dict[str, Tier1OOFRun] = {}
        for etf_id, local in frame.groupby("etf_id", sort=True):
            local = local.reset_index(drop=True)
            folds = chronological_purged_folds(
                local[["t0", "t1"]], n_splits=outer_splits
            )
            serialized_folds = [(train.tolist(), valid.tolist()) for train, valid in folds]
            validate_fold_feature_coverage(local, serialized_folds, feature_columns)
            predictions = oof_logistic_predictions(
                local,
                serialized_folds,
                feature_columns,
                model_family=model_family,
                categorical_columns=categorical_columns,
                candidate_threshold_objective=candidate_threshold_objective,
                minimum_candidate_weight_share=minimum_candidate_weight_share,
                trading_sessions=self.trading_sessions,
                uniqueness_entity_column=None,
            )
            runs[str(etf_id)] = Tier1OOFRun(
                training_frame=local,
                predictions=predictions,
                handoff=build_tier1_handoff(local, predictions),
                folds=folds,
            )
        if not runs:
            raise ValueError("ETF-local research frame has no ETF partitions")
        return Tier1LocalOOFRuns(by_etf=runs)

    def _build_training_frame(
        self,
        feature_columns: list[str],
        research_t0_end: str | pd.Timestamp | None,
        research_outcome_before: str | pd.Timestamp | None,
    ) -> pd.DataFrame:
        """Load immutable inputs and apply the shared research boundary."""
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
                on=["etf_id", "bar_id"], how="inner", suffixes=("_base", "_extension"),
                validate="one_to_one",
            )
            if len(clock) != len(features):
                raise ValueError("Tier 1 feature extension does not cover every AFML feature key")
            base_time = pd.to_datetime(clock["feature_available_at_base"], errors="coerce", utc=True)
            extension_time = pd.to_datetime(clock["feature_available_at_extension"], errors="coerce", utc=True)
            if base_time.isna().any() or extension_time.isna().any() or not base_time.equals(extension_time):
                raise ValueError("Tier 1 feature extension availability must equal AFML feature availability")
            extension_columns = [column for column in extension.columns if column not in required]
            overlap = set(extension_columns).intersection(features.columns)
            if overlap:
                raise ValueError(f"Tier 1 feature extension overlaps AFML columns: {sorted(overlap)}")
            features = features.merge(
                extension[["etf_id", "bar_id", *extension_columns]],
                on=["etf_id", "bar_id"], how="inner", validate="one_to_one",
            )
        frame = build_directional_training_frame(targets, features, feature_columns)
        if (research_t0_end is None) != (research_outcome_before is None):
            raise ValueError("research_t0_end and research_outcome_before must be supplied together")
        if research_t0_end is not None and research_outcome_before is not None:
            t0_end = pd.Timestamp(research_t0_end).normalize()
            outcome_before = pd.Timestamp(research_outcome_before).normalize()
            if t0_end >= outcome_before:
                raise ValueError("research t0 end must precede sealed outcome boundary")
            frame = frame.loc[frame["t0"].le(t0_end) & frame["t1"].lt(outcome_before)].reset_index(drop=True)
            if frame.empty:
                raise ValueError("research cut-off excludes every resolved target")
        return frame
