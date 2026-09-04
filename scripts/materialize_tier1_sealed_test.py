"""One-time selected-ETF Tier 1 sealed evaluation without exposing other test rows."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.governance.trials import TrialRegistry
from etf_tricks.tier1.artifact import write_sealed_artifact
from etf_tricks.tier1.long_history import feature_columns_for
from etf_tricks.tier1.research import build_directional_training_frame
from etf_tricks.tier1.sealed import predict_sealed, split_training_and_sealed_frames


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_object(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_frame(afml_root: Path, target_root: Path, extension_root: Path, feature_columns: list[str]) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    features = pd.read_parquet(afml_root / "tables" / "features.parquet")
    extension = pd.read_parquet(extension_root / "features.parquet")
    required = {"etf_id", "bar_id", "feature_available_at"}
    if missing := required.difference(extension.columns):
        raise ValueError(f"feature extension missing columns: {sorted(missing)}")
    base_clock = features[["etf_id", "bar_id", "feature_available_at"]]
    clock = base_clock.merge(
        extension[["etf_id", "bar_id", "feature_available_at"]],
        on=["etf_id", "bar_id"], how="inner", validate="one_to_one", suffixes=("_base", "_extension"),
    )
    if len(clock) != len(features):
        raise ValueError("feature extension does not cover AFML keys")
    base_time = pd.to_datetime(clock["feature_available_at_base"], utc=True)
    extension_time = pd.to_datetime(clock["feature_available_at_extension"], utc=True)
    if not base_time.equals(extension_time):
        raise ValueError("feature extension availability clock differs from AFML")
    extension_columns = [column for column in extension.columns if column not in required]
    if overlap := set(extension_columns).intersection(features.columns):
        raise ValueError(f"feature extension overlaps AFML columns: {sorted(overlap)}")
    features = features.merge(extension[["etf_id", "bar_id", *extension_columns]], on=["etf_id", "bar_id"], how="inner", validate="one_to_one")
    metadata = _read_json(afml_root / "metadata.json")
    sessions = pd.DatetimeIndex(pd.to_datetime(metadata.get("trading_sessions"), errors="coerce"))
    if sessions.empty or sessions.isna().any() or sessions.has_duplicates:
        raise ValueError("AFML metadata requires valid trading sessions")
    return build_directional_training_frame(pd.read_parquet(target_root / "targets.parquet"), features, feature_columns), sessions.sort_values()


def _sealed_metrics(sealed_frame: pd.DataFrame, predictions: pd.DataFrame) -> dict[str, float | int]:
    joined = sealed_frame[["event_id", "y_direction", "net_log_return"]].merge(predictions[["event_id", "p1", "candidate_indicator"]], on="event_id", validate="one_to_one")
    target = (joined["y_direction"] == 1).astype(int)
    chosen = joined.loc[joined["candidate_indicator"]]
    return {
        "sealed_rows": int(len(joined)),
        "auc": float(roc_auc_score(target, joined["p1"])) if target.nunique() == 2 else None,
        "brier": float(brier_score_loss(target, joined["p1"])),
        "log_loss": float(log_loss(target, joined["p1"], labels=[0, 1])),
        "candidate_count": int(len(chosen)),
        "candidate_positive_rate": float((chosen["y_direction"] == 1).mean()) if not chosen.empty else None,
        "base_positive_rate": float(target.mean()),
        "candidate_mean_net_log_return": float(chosen["net_log_return"].mean()) if not chosen.empty else None,
        "base_mean_net_log_return": float(joined["net_log_return"].mean()),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--feature-extension-root", required=True)
    parser.add_argument("--registry-path", default=".artifacts/afml_governance/tier1_trials.jsonl")
    parser.add_argument("--prediction-output-root", required=True)
    parser.add_argument("--report-output-root", required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--selected-etf-id", required=True)
    parser.add_argument("--feature-set", required=True)
    parser.add_argument("--research-t0-end", default="2024-12-31")
    parser.add_argument("--sealed-start", default="2025-01-01")
    parser.add_argument(
        "--outcome-access-boundary",
        required=True,
        help="Pre-sealed JSON boundary proving which outcomes were observable before the test interval.",
    )
    parser.add_argument("--effective-independent-trial-count", type=float, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    roots = {"afml": Path(args.afml_root), "target": Path(args.target_root), "extension": Path(args.feature_extension_root)}
    outcome_access_boundary = _read_json(Path(args.outcome_access_boundary))
    manifests = {f"{name}_manifest": root / "manifest.json" for name, root in roots.items()}
    if missing := [str(path) for path in manifests.values() if not path.is_file()]:
        raise ValueError(f"missing immutable input manifest: {missing}")
    prediction_root, report_root = Path(args.prediction_output_root), Path(args.report_output_root)
    if prediction_root.exists() or report_root.exists():
        raise FileExistsError("sealed output already exists for this lineage")
    features = feature_columns_for(args.feature_set)
    config = {"model_family": "hist_gradient_boosting", "feature_set": args.feature_set, "feature_columns": features, "candidate_threshold_objective": "economic_net_log_return", "selected_etf_id": args.selected_etf_id, "research_t0_end": args.research_t0_end, "sealed_start": args.sealed_start}
    upstream = {name: _sha256(path) for name, path in manifests.items()}
    registry = TrialRegistry(args.registry_path)
    record = {
        "trial_id": args.trial_id,
        "parent_trial_id": "tier1-hgb-base15-cost-audited-2005-2024-v1-result",
        "created_at": _now(), "completed_at": None,
        "research_question": "Does the selected ETF retain its fixed ETF-local HGB-base15 Tier 1 signal in the once-only sealed interval?",
        "hypothesis": "The selected ETF's frozen local model may retain discrimination and net candidate return without sealed-time selection or recalibration.",
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "upstream_artifact_hashes": upstream,
        "feature_set_hash": _hash_object(features),
        "label_config_hash": _hash_object(_read_json(roots["target"] / "manifest.json")["metadata"]["target_config"]),
        "tier1_config_hash": _hash_object(config), "tier2_config_hash": None, "allocation_config_hash": None,
        "execution_cost_policy_hash": _hash_object({"buy_cost_rate": 0.001425, "sell_cost_rate": 0.003, "cost_policy_id": "tier1-proportional-v1"}),
        "fold_definition_hash": _hash_object(config),
        "train_validation_test_boundaries": {"research_t0_end": args.research_t0_end, "research_outcome_before": args.sealed_start, "sealed_test_start": args.sealed_start, "sealed_test_end": "2026-07-07", "selected_etf_id": args.selected_etf_id},
        "raw_trial_count": int(args.effective_independent_trial_count), "effective_independent_trial_count": float(args.effective_independent_trial_count),
        "etf_scope": args.selected_etf_id, "model_scope": "ETF_LOCAL", "validation_metrics": {}, "selection_status": "REGISTERED", "selection_reason": "Registered before any ETF-local sealed prediction; the effective trial count includes prior selection alternatives.",
    }
    registry.append(record)
    frame, sessions = _load_frame(roots["afml"], roots["target"], roots["extension"], features)
    training, sealed = split_training_and_sealed_frames(frame, research_t0_end=args.research_t0_end, sealed_start=args.sealed_start, selected_etf_id=args.selected_etf_id, outcome_access_boundary=outcome_access_boundary)
    predictions = predict_sealed(training, sealed, features, model_family="hist_gradient_boosting", trading_sessions=sessions, outcome_access_boundary=outcome_access_boundary)
    metadata = {**upstream, "trial_id": args.trial_id, "selected_etf_id": args.selected_etf_id, "sealed_start": args.sealed_start, "outcome_access_boundary": outcome_access_boundary, "config": config, "training_rows": len(training), "sealed_rows": len(sealed)}
    prediction_manifest = write_sealed_artifact(predictions, prediction_root, metadata)
    metrics = _sealed_metrics(sealed, predictions)
    report_root.mkdir(parents=True)
    report_path = report_root / "report.json"
    report = {"trial_id": args.trial_id, "selected_etf_id": args.selected_etf_id, "metrics": metrics, "tier2_permitted": False, "tier3_permitted": False, "reason": "A selected-ETF sealed result cannot override the failed all-pool Tier 1 gate or admit downstream layers."}
    report_path.write_text(json.dumps(report, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    report_manifest = {"schema_version": "tier1-sealed-report-v1", "report": {"path": report_path.name, "sha256": _sha256(report_path)}}
    (report_root / "manifest.json").write_text(json.dumps(report_manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    result = {**record, "trial_id": f"{args.trial_id}-result", "parent_trial_id": args.trial_id, "created_at": _now(), "completed_at": _now(), "selection_status": "SEALED_EVALUATED", "selection_reason": "Sealed result recorded; it cannot admit Tier 2/3 until the independent all-pool Tier 1 gate and final DSR gate pass.", "upstream_artifact_hashes": {**upstream, "sealed_prediction_manifest": _sha256(prediction_root / "manifest.json"), "sealed_report_manifest": _sha256(report_root / "manifest.json")}, "validation_metrics": metrics}
    registry.append(result)
    print({"trial_id": args.trial_id, "selected_etf_id": args.selected_etf_id, "metrics": metrics, "prediction_sha256": prediction_manifest["tables"]["sealed_predictions"]["sha256"], "report_sha256": report_manifest["report"]["sha256"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
