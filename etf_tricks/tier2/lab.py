"""Artifact-backed ETF-local Tier 2 OOF research orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd

from etf_tricks.tier1.splits import chronological_purged_folds

from .frame import build_meta_training_frame
from .model import oof_meta_predictions


@dataclass(frozen=True)
class Tier2OOFRun:
    training_frame: pd.DataFrame
    predictions: pd.DataFrame
    handoff: pd.DataFrame
    folds: list[tuple]


class Tier2Lab:
    """Load immutable AFML inputs and create one ETF-local Tier 2 OOF run."""

    def __init__(
        self,
        afml_root: str | Path,
        target_root: str | Path,
        feature_extension_root: str | Path | None = None,
        trading_sessions: pd.DatetimeIndex | None = None,
    ) -> None:
        self.afml_root = Path(afml_root)
        self.target_root = Path(target_root)
        self.feature_extension_root = None if feature_extension_root is None else Path(feature_extension_root)
        self.trading_sessions = trading_sessions

    @classmethod
    def from_artifacts(
        cls,
        afml_root: str | Path,
        target_root: str | Path,
        feature_extension_root: str | Path | None = None,
    ) -> "Tier2Lab":
        metadata_path = Path(afml_root) / "metadata.json"
        if not metadata_path.is_file():
            raise ValueError("AFML artifact missing metadata.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        sessions = pd.DatetimeIndex(pd.to_datetime(metadata.get("trading_sessions"), errors="coerce"))
        if sessions.empty or sessions.isna().any() or sessions.has_duplicates:
            raise ValueError("AFML metadata requires valid unique trading_sessions")
        return cls(afml_root, target_root, feature_extension_root, sessions.normalize().sort_values())

    def run_oof(
        self,
        tier1_oof: pd.DataFrame,
        feature_columns: list[str],
        outer_splits: int = 3,
        model_family: str = "logistic_regression",
    ) -> Tier2OOFRun:
        if "p1" not in feature_columns:
            raise ValueError("Tier 2 feature columns must include Tier 1 OOF p1")
        source_features = [column for column in feature_columns if column != "p1"]
        frame = build_meta_training_frame(
            tier1_oof,
            pd.read_parquet(self.target_root / "targets.parquet"),
            self._features(),
            source_features,
        )
        if frame.empty:
            raise ValueError("Tier 2 has no Tier 1 OOF candidates")
        folds = chronological_purged_folds(frame[["t0", "t1"]], n_splits=outer_splits)
        predictions = oof_meta_predictions(
            frame,
            [(train.tolist(), valid.tolist()) for train, valid in folds],
            feature_columns,
            model_family=model_family,
            trading_sessions=self.trading_sessions,
        )
        return Tier2OOFRun(frame, predictions, _build_handoff(frame, predictions), folds)

    def _features(self) -> pd.DataFrame:
        features = pd.read_parquet(self.afml_root / "tables" / "features.parquet")
        if self.feature_extension_root is None:
            return features
        extension = pd.read_parquet(self.feature_extension_root / "features.parquet")
        required = {"etf_id", "bar_id", "feature_available_at"}
        if missing := required.difference(extension.columns):
            raise ValueError(f"Tier 2 feature extension missing columns: {sorted(missing)}")
        if extension.duplicated(["etf_id", "bar_id"]).any():
            raise ValueError("Tier 2 feature extension has duplicate keys")
        extension_columns = [column for column in extension.columns if column not in required]
        if overlap := set(extension_columns).intersection(features.columns):
            raise ValueError(f"Tier 2 feature extension overlaps AFML columns: {sorted(overlap)}")
        clock = features[["etf_id", "bar_id", "feature_available_at"]].merge(
            extension[["etf_id", "bar_id", "feature_available_at"]],
            on=["etf_id", "bar_id"], how="inner", validate="one_to_one", suffixes=("_base", "_extension"),
        )
        if len(clock) != len(features):
            raise ValueError("Tier 2 feature extension does not cover every AFML feature key")
        left = pd.to_datetime(clock["feature_available_at_base"], errors="coerce", utc=True)
        right = pd.to_datetime(clock["feature_available_at_extension"], errors="coerce", utc=True)
        if left.isna().any() or right.isna().any() or not left.equals(right):
            raise ValueError("Tier 2 feature extension availability must equal AFML feature availability")
        return features.merge(extension[["etf_id", "bar_id", *extension_columns]], on=["etf_id", "bar_id"], how="inner", validate="one_to_one")


def _build_handoff(frame: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    """Emit Tier 2 OOF predictions without labels, horizons or realized PnL."""
    if not frame.index.equals(predictions.index):
        raise ValueError("Tier 2 frame and predictions must have identical indexes")
    joined = frame[["event_id", "etf_id", "t0_bar_id", "tier2_decision_available_at"]].join(predictions)
    handoff = joined.loc[joined["p2"].notna()].copy()
    if not handoff["prediction_kind"].eq("OOF_CALIBRATED").all():
        raise ValueError("Tier 2 handoff accepts calibrated OOF predictions only")
    if handoff[["accepted", "acceptance_reason"]].isna().any().any():
        raise ValueError("Tier 2 OOF predictions require acceptance metadata")
    return handoff.loc[:, ["event_id", "etf_id", "t0_bar_id", "p2", "accepted", "acceptance_threshold", "acceptance_reason", "prediction_kind", "tier2_decision_available_at"]].sort_values(
        ["tier2_decision_available_at", "etf_id", "t0_bar_id"], kind="stable"
    ).reset_index(drop=True)
