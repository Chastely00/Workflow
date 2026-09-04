"""Materialize pre-registered, ETF-local, research-only Tier 2 OOF evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_tricks.governance.trials import TrialRegistry
from etf_tricks.tier1.long_history import feature_columns_for
from etf_tricks.tier2.artifact import write_tier2_oof_artifact
from etf_tricks.tier2.diagnostics import summarize_tier2_oof
from etf_tricks.tier2.lab import Tier2Lab


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--afml-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--feature-extension-root", required=True)
    parser.add_argument("--tier1-oof-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--etf-id", action="append", required=True, dest="etf_ids")
    parser.add_argument("--trial-prefix", required=True)
    parser.add_argument("--feature-set", default="hgb_base_15_v1")
    parser.add_argument("--model-family", choices=("logistic_regression", "hist_gradient_boosting"), required=True)
    parser.add_argument("--outer-splits", type=int, default=3)
    parser.add_argument("--effective-trial-count-base", type=float, required=True)
    parser.add_argument("--registry-path", default=".artifacts/afml_governance/tier1_trials.jsonl")
    return parser.parse_args()


def main() -> int:
    args = _args()
    output = Path(args.output_root)
    if output.exists():
        raise FileExistsError(f"output root already exists: {output}")
    etf_ids = sorted(set(args.etf_ids))
    if len(etf_ids) != len(args.etf_ids):
        raise ValueError("ETF-local selection contains duplicates")
    if args.effective_trial_count_base < 0:
        raise ValueError("effective trial count base must be nonnegative")
    roots = {
        "afml": Path(args.afml_root), "target": Path(args.target_root),
        "extension": Path(args.feature_extension_root), "tier1_oof": Path(args.tier1_oof_root),
    }
    targets = pd.read_parquet(roots["target"] / "targets.parquet")
    manifests = {name: path / "manifest.json" for name, path in roots.items() if name != "tier1_oof"}
    if missing := [str(path) for path in manifests.values() if not path.is_file()]:
        raise ValueError(f"missing immutable input manifest: {missing}")
    features = feature_columns_for(args.feature_set) + ["p1"]
    registry = TrialRegistry(args.registry_path)
    # Registration establishes the pre-result sample-adequacy probe.  It does
    # not consume DSR budget unless a comparable calibrated OOF result exists.
    preflight_count = float(args.effective_trial_count_base)
    common = {
        "model_scope": "ETF_LOCAL", "model_family": args.model_family,
        "feature_set": args.feature_set, "feature_columns": features,
        "outer_splits": args.outer_splits, "research_only": True,
        "sealed_status": "NOT_SEALED", "acceptance_threshold_objective": "f1",
    }
    lab = Tier2Lab.from_artifacts(roots["afml"], roots["target"], roots["extension"])
    output.mkdir(parents=True)
    for etf_id in etf_ids:
        tier1_root = roots["tier1_oof"] / etf_id / "oof"
        manifest_path = tier1_root / "manifest.json"
        table_path = tier1_root / "oof_handoff.parquet"
        if not manifest_path.is_file() or not table_path.is_file():
            raise ValueError(f"missing Tier 1 OOF artifact for ETF: {etf_id}")
        tier1_oof = pd.read_parquet(table_path)
        if not tier1_oof["etf_id"].eq(etf_id).all():
            raise ValueError(f"Tier 1 OOF artifact has wrong ETF scope: {etf_id}")
        upstream = {name: _sha256(path) for name, path in manifests.items()}
        upstream["tier1_oof_manifest"] = _sha256(manifest_path)
        trial_id = f"{args.trial_prefix}-{etf_id}"
        record = {
            "trial_id": trial_id, "parent_trial_id": f"tier1-etf-local-hgb-base15-longhistory-2005-2024-v1-{etf_id}-result",
            "created_at": _now(), "completed_at": None,
            "research_question": "Can an ETF-local meta-model filter that ETF's own OOF Tier 1 long candidates?",
            "hypothesis": "PIT state plus Tier 1 OOF probability may improve acceptance precision among the ETF's existing long candidates.",
            "code_commit": _commit(), "upstream_artifact_hashes": upstream,
            "feature_set_hash": _hash(features), "label_config_hash": _hash({"y_meta": "1[y_direction=+1] among Tier1 OOF candidates"}),
            "tier1_config_hash": _sha256(manifest_path), "tier2_config_hash": _hash({**common, "etf_scope": etf_id}),
            "allocation_config_hash": None, "execution_cost_policy_hash": _hash({"inherited_from_tier1_label": True}),
            "fold_definition_hash": _hash({**common, "etf_scope": etf_id}),
            "train_validation_test_boundaries": {"research_t0_start": "2005-01-01", "research_t0_end": "2024-12-31", "sealed_status": "NOT_SEALED"},
            "etf_scope": etf_id, "model_scope": "ETF_LOCAL", "raw_trial_count": int(preflight_count),
            "effective_independent_trial_count": preflight_count, "validation_metrics": {},
            "selection_status": "REGISTERED", "selection_reason": "Registered before Tier 2 fitting as a non-performance sample-adequacy probe; research-only and not sealed.",
        }
        registry.append(record)
        run = lab.run_oof(tier1_oof, features, outer_splits=args.outer_splits, model_family=args.model_family)
        metrics = summarize_tier2_oof(run.training_frame, run.predictions, targets)
        diagnostics_root = output / etf_id / "diagnostics"
        diagnostics_root.mkdir(parents=True)
        pd.DataFrame([metrics]).to_parquet(diagnostics_root / "metrics.parquet", index=False)
        diagnostic_manifest = {"schema_version": "tier2-diagnostics-v1", "research_only": True, "sealed_status": "NOT_SEALED", "table_sha256": _sha256(diagnostics_root / "metrics.parquet")}
        (diagnostics_root / "manifest.json").write_text(json.dumps(diagnostic_manifest, sort_keys=True), encoding="utf-8")
        if run.handoff.empty:
            registry.append({
                **record, "trial_id": f"{trial_id}-result", "parent_trial_id": trial_id,
                "created_at": _now(), "completed_at": _now(), "validation_metrics": metrics,
                "upstream_artifact_hashes": {**upstream, "diagnostics_manifest": _sha256(diagnostics_root / "manifest.json")},
                "selection_status": "INSUFFICIENT_MATURE_EVENTS", "selection_reason": "No fold emitted calibrated Tier 2 OOF predictions; no synthetic handoff was written.",
            })
            print({"etf_id": etf_id, **metrics, "status": "INSUFFICIENT_MATURE_EVENTS", "oof_sha256": None})
            continue
        # A nonempty calibrated OOF handoff is a comparable performance
        # alternative and therefore consumes one conservative DSR trial.
        result_count = float(args.effective_trial_count_base + len(etf_ids))
        artifact_metadata = {**upstream, **common, "etf_scope": etf_id, "trial_id": trial_id}
        manifest = write_tier2_oof_artifact(run.handoff, output / etf_id / "oof", artifact_metadata)
        registry.append({
            **record, "trial_id": f"{trial_id}-result", "parent_trial_id": trial_id,
            "created_at": _now(), "completed_at": _now(), "validation_metrics": metrics,
            "raw_trial_count": int(result_count), "effective_independent_trial_count": result_count,
            "upstream_artifact_hashes": {**upstream, "tier2_oof_manifest": _sha256(output / etf_id / "oof" / "manifest.json"), "diagnostics_manifest": _sha256(diagnostics_root / "manifest.json")},
            "selection_status": "RESEARCH_ONLY", "selection_reason": "Tier 2 OOF materialized; NOT_SEALED and no Tier 3/paper admission.",
        })
        print({"etf_id": etf_id, **metrics, "oof_sha256": manifest["tables"]["oof_handoff"]["sha256"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
