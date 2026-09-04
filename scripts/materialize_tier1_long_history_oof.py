"""Pre-registered, sealed-safe Tier 1 long-history OOF diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.governance.trials import TrialRegistry, write_tier1_gate_report
from etf_tricks.tier1.artifact import write_oof_artifact
from etf_tricks.tier1.diagnostics import summarize_per_etf_oof
from etf_tricks.tier1.lab import Tier1Lab
from etf_tricks.tier1.long_history import (
    feature_columns_for,
    validate_long_history_research_frame,
)


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_object(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _code_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _metrics(frame: pd.DataFrame, predictions: pd.DataFrame) -> dict[str, float | int]:
    observed = predictions["p1"].notna()
    if not observed.any():
        raise ValueError("long-history OOF emitted no validation predictions")
    joined = frame.loc[observed, ["y_direction", "net_log_return"]].join(
        predictions.loc[observed, ["p1", "is_candidate"]]
    )
    target = (joined["y_direction"] == 1).astype(int)
    if target.nunique() != 2:
        raise ValueError("long-history OOF requires both validation classes")
    candidate = joined["is_candidate"].astype(bool)
    chosen = joined.loc[candidate]
    return {
        "oof_rows": int(len(joined)),
        "auc": float(roc_auc_score(target, joined["p1"])),
        "brier": float(brier_score_loss(target, joined["p1"])),
        "log_loss": float(log_loss(target, joined["p1"], labels=[0, 1])),
        "candidate_count": int(candidate.sum()),
        "candidate_positive_rate": float((chosen["y_direction"] == 1).mean()) if not chosen.empty else float("nan"),
        "base_positive_rate": float(target.mean()),
        "candidate_mean_net_log_return": float(chosen["net_log_return"].mean()) if not chosen.empty else float("nan"),
        "base_mean_net_log_return": float(joined["net_log_return"].mean()),
    }


def _gate(metrics: dict[str, float | int], trial_id: str, effective_trial_count: float) -> dict[str, object]:
    reasons: list[str] = []
    if not float(metrics["auc"]) > 0.5:
        reasons.append("oof_auc_not_above_0_5")
    if not int(metrics["candidate_count"]) > 0:
        reasons.append("no_oof_candidates")
    elif not float(metrics["candidate_positive_rate"]) > float(metrics["base_positive_rate"]):
        reasons.append("candidate_positive_rate_not_above_base")
    if not int(metrics["candidate_count"]) > 0 or not float(metrics["candidate_mean_net_log_return"]) > float(metrics["base_mean_net_log_return"]):
        reasons.append("candidate_net_return_not_above_base")
    passed = not reasons
    return {
        "trial_id": trial_id,
        "effective_independent_trial_count": effective_trial_count,
        "metrics": metrics,
        "reasons": reasons,
        "status": "PASSED" if passed else "FAILED",
        "tier2_permitted": passed,
        "tier3_permitted": passed,
    }


def _write_diagnostics(
    diagnostics: pd.DataFrame,
    output: Path,
    metadata: dict[str, object],
) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(f"diagnostic output already exists: {output}")
    output.mkdir(parents=True)
    table = output / "per_etf_oof.parquet"
    diagnostics.to_parquet(table, index=False)
    manifest = {
        "schema_version": "tier1-long-history-diagnostics-v1",
        "metadata": metadata,
        "tables": {"per_etf_oof": {"path": table.name, "row_count": len(diagnostics), "sha256": _sha256_path(table)}},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--feature-extension-root", required=True)
    parser.add_argument("--registry-path", default=".artifacts/afml_governance/tier1_trials.jsonl")
    parser.add_argument("--oof-output-root", required=True)
    parser.add_argument("--diagnostics-output-root", required=True)
    parser.add_argument("--gate-output-root", required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--feature-set", required=True)
    parser.add_argument("--research-t0-end", default="2024-12-31")
    parser.add_argument("--sealed-start", default="2025-01-01")
    parser.add_argument("--outer-splits", type=int, default=3)
    parser.add_argument("--effective-independent-trial-count", type=float, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    roots = {
        "afml": Path(args.afml_root),
        "target": Path(args.target_root),
        "extension": Path(args.feature_extension_root),
    }
    manifests = {
        "afml_manifest": roots["afml"] / "manifest.json",
        "target_manifest": roots["target"] / "manifest.json",
        "feature_extension_manifest": roots["extension"] / "manifest.json",
    }
    if missing := [str(path) for path in manifests.values() if not path.is_file()]:
        raise ValueError(f"missing immutable input manifest: {missing}")
    outputs = [Path(args.oof_output_root), Path(args.diagnostics_output_root), Path(args.gate_output_root)]
    if any(path.exists() for path in outputs):
        raise FileExistsError("long-history output root already exists")
    if args.effective_independent_trial_count <= 0:
        raise ValueError("effective-independent-trial-count must be positive")
    feature_columns = feature_columns_for(args.feature_set)

    config = {
        "model_family": "hist_gradient_boosting",
        "feature_set": args.feature_set,
        "feature_columns": feature_columns,
        "categorical_columns": ["etf_id"],
        "candidate_threshold_objective": "economic_net_log_return",
        "minimum_candidate_weight_share": 0.10,
        "outer_splits": args.outer_splits,
        "calibration_splits": 2,
        "research_t0_end": args.research_t0_end,
        "sealed_start": args.sealed_start,
        "fold_policy": "chronological_expanding_event_end_purged; no future training rows makes forward embargo structurally zero",
    }
    upstream = {name: _sha256_path(path) for name, path in manifests.items()}
    registry = TrialRegistry(args.registry_path)
    trial_record = {
        "trial_id": args.trial_id,
        "parent_trial_id": "tier1-hgb-chip-flow-cost-audited-2020-2026-v2-result",
        "created_at": _utc_now(),
        "completed_at": None,
        "research_question": "Does the fixed nonlinear Tier 1 feature contract retain promotable long-history OOF discrimination before the untouched 2025-2026 sealed interval?",
        "hypothesis": "The pre-registered shallow HGB contract may show stable PIT nonlinear interactions over a longer mature-event history without changing barriers, costs, features, or threshold rule.",
        "code_commit": _code_commit(),
        "upstream_artifact_hashes": upstream,
        "feature_set_hash": _sha256_object(feature_columns),
        "label_config_hash": _sha256_object(json.loads((roots["target"] / "manifest.json").read_text(encoding="utf-8"))["metadata"]["target_config"]),
        "tier1_config_hash": _sha256_object(config),
        "tier2_config_hash": None,
        "allocation_config_hash": None,
        "execution_cost_policy_hash": _sha256_object({"buy_cost_rate": 0.001425, "sell_cost_rate": 0.003, "cost_policy_id": "tier1-proportional-v1"}),
        "fold_definition_hash": _sha256_object(config),
        "train_validation_test_boundaries": {"research_t0_end": args.research_t0_end, "research_outcome_before": args.sealed_start, "sealed_test_start": args.sealed_start, "sealed_test_end": "2026-07-07"},
        "etf_scope": "ALL_ETFS",
        "model_scope": "PANEL_BENCHMARK",
        "raw_trial_count": int(args.effective_independent_trial_count),
        "effective_independent_trial_count": float(args.effective_independent_trial_count),
        "validation_metrics": {},
        "selection_status": "REGISTERED",
        "selection_reason": "Registered before long-history OOF fitting; model, feature set, costs, and threshold policy are fixed from the prior governed lineage.",
    }
    registry.append(trial_record)

    lab = Tier1Lab.from_artifacts(roots["afml"], roots["target"], roots["extension"])
    try:
        run = lab.run_oof(
            feature_columns,
            outer_splits=args.outer_splits,
            model_family="hist_gradient_boosting",
            categorical_columns=("etf_id",),
            candidate_threshold_objective="economic_net_log_return",
            research_t0_end=args.research_t0_end,
            research_outcome_before=args.sealed_start,
        )
    except ValueError as error:
        if "declared feature absent" not in str(error):
            raise
        registry.append(
            {
                **trial_record,
                "trial_id": f"{args.trial_id}-invalid-input-coverage",
                "parent_trial_id": args.trial_id,
                "created_at": _utc_now(),
                "completed_at": _utc_now(),
                "selection_status": "INVALID_INPUT_COVERAGE",
                "selection_reason": str(error),
            }
        )
        print({"trial_id": args.trial_id, "selection_status": "INVALID_INPUT_COVERAGE", "reason": str(error)})
        return 2
    boundary = validate_long_history_research_frame(
        run.training_frame,
        research_t0_end=args.research_t0_end,
        sealed_start=args.sealed_start,
    )
    metrics = _metrics(run.training_frame, run.predictions)
    if not all(np.isfinite(value) for value in metrics.values() if isinstance(value, float)):
        raise ValueError("long-history OOF metrics are non-finite")
    oof_metadata = {**upstream, "trial_id": args.trial_id, "config": config, "boundary": boundary}
    oof_manifest = write_oof_artifact(run.handoff, args.oof_output_root, oof_metadata)
    expected_etfs = sorted(pd.read_parquet(roots["target"] / "targets.parquet", columns=["etf_id"])["etf_id"].unique())
    diagnostics = summarize_per_etf_oof(
        run.training_frame,
        run.predictions,
        [(train.tolist(), valid.tolist()) for train, valid in run.folds],
        expected_etf_ids=expected_etfs,
    )
    diagnostic_manifest = _write_diagnostics(diagnostics, Path(args.diagnostics_output_root), oof_metadata)
    gate = _gate(metrics, args.trial_id, float(args.effective_independent_trial_count))
    gate_manifest = write_tier1_gate_report(gate, args.gate_output_root)
    result_record = {**trial_record, "trial_id": f"{args.trial_id}-result", "parent_trial_id": args.trial_id, "created_at": _utc_now(), "completed_at": _utc_now(), "selection_status": "SELECTED" if gate["status"] == "PASSED" else "REJECTED", "selection_reason": "Long-history OOF gate passed." if gate["status"] == "PASSED" else "Long-history OOF gate failed: " + ", ".join(gate["reasons"]), "upstream_artifact_hashes": {**upstream, "oof_manifest": _sha256_path(Path(args.oof_output_root) / "manifest.json"), "diagnostics_manifest": _sha256_path(Path(args.diagnostics_output_root) / "manifest.json"), "gate_manifest": _sha256_path(Path(args.gate_output_root) / "manifest.json")}, "validation_metrics": metrics}
    registry.append(result_record)
    print({"trial_id": args.trial_id, "boundary": boundary, "metrics": metrics, "gate_status": gate["status"], "oof_sha256": oof_manifest["tables"]["oof_handoff"]["sha256"], "diagnostics_sha256": diagnostic_manifest["tables"]["per_etf_oof"]["sha256"], "gate_sha256": gate_manifest["report"]["sha256"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
