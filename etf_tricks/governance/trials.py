from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any


_REQUIRED_FIELDS = {
    "trial_id",
    "parent_trial_id",
    "created_at",
    "completed_at",
    "research_question",
    "hypothesis",
    "code_commit",
    "upstream_artifact_hashes",
    "feature_set_hash",
    "label_config_hash",
    "tier1_config_hash",
    "tier2_config_hash",
    "allocation_config_hash",
    "execution_cost_policy_hash",
    "fold_definition_hash",
    "train_validation_test_boundaries",
    "raw_trial_count",
    "effective_independent_trial_count",
    "validation_metrics",
    "selection_status",
    "selection_reason",
}


class TrialRegistry:
    """Append-only JSONL registry for every performance-observed trial."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, record: dict[str, Any]) -> None:
        fields = set(record)
        if fields != _REQUIRED_FIELDS:
            raise ValueError(f"trial record fields differ from contract; missing={sorted(_REQUIRED_FIELDS - fields)}, extra={sorted(fields - _REQUIRED_FIELDS)}")
        if not isinstance(record["trial_id"], str) or not record["trial_id"]:
            raise ValueError("trial_id must be a nonempty string")
        existing = self._records()
        if any(item["trial_id"] == record["trial_id"] for item in existing):
            raise ValueError(f"duplicate trial_id: {record['trial_id']}")
        try:
            encoded = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("trial record must be canonical JSON serializable") from exc
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            records.append(json.loads(line))
        return records


def write_tier1_gate_report(report: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    """Persist an immutable Tier 1 gate decision before downstream work begins."""
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"Tier 1 gate output already exists: {output}")
    if report.get("status") not in {"PASSED", "FAILED"}:
        raise ValueError("Tier 1 gate report requires status PASSED or FAILED")
    if report["status"] == "FAILED" and (report.get("tier2_permitted") or report.get("tier3_permitted")):
        raise ValueError("a failed Tier 1 gate cannot permit downstream layers")
    try:
        encoded = json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Tier 1 gate report must be canonical JSON serializable") from exc
    output.mkdir(parents=True)
    path = output / "report.json"
    path.write_text(encoded, encoding="utf-8")
    manifest = {"schema_version": "tier1-gate-v1", "report": {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return manifest
