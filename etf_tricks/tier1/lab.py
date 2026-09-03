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

    def __init__(self, afml_root: Path, target_root: Path) -> None:
        self.afml_root = afml_root
        self.target_root = target_root

    @classmethod
    def from_artifacts(cls, afml_root: str | Path, target_root: str | Path) -> "Tier1Lab":
        afml = Path(afml_root)
        targets = Path(target_root)
        if not (afml / "tables" / "features.parquet").is_file():
            raise ValueError("AFML artifact missing tables/features.parquet")
        if not (targets / "targets.parquet").is_file():
            raise ValueError("Tier 1 target artifact missing targets.parquet")
        return cls(afml, targets)

    def run_oof(
        self,
        feature_columns: list[str],
        outer_splits: int = 3,
        model_family: str = "logistic_regression",
        categorical_columns: tuple[str, ...] = (),
    ) -> Tier1OOFRun:
        targets = pd.read_parquet(self.target_root / "targets.parquet")
        features = pd.read_parquet(self.afml_root / "tables" / "features.parquet")
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
