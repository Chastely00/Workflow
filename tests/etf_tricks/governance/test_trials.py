import json

from etf_tricks.governance.trials import TrialRegistry


def test_trial_registry_appends_a_complete_immutable_record(tmp_path) -> None:
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    record = {
        "trial_id": "tier1-logistic-v1",
        "parent_trial_id": None,
        "created_at": "2026-09-03T00:00:00Z",
        "completed_at": "2026-09-03T00:01:00Z",
        "research_question": "Can calibrated logistic produce positive long candidates?",
        "hypothesis": "The PIT feature set ranks positive barriers above negative barriers.",
        "code_commit": "a" * 40,
        "upstream_artifact_hashes": {"afml": "b" * 64},
        "feature_set_hash": "c" * 64,
        "label_config_hash": "d" * 64,
        "tier1_config_hash": "e" * 64,
        "tier2_config_hash": None,
        "allocation_config_hash": None,
        "execution_cost_policy_hash": "f" * 64,
        "fold_definition_hash": "0" * 64,
        "train_validation_test_boundaries": {"validation_end": "2026-04-10"},
        "raw_trial_count": 1,
        "effective_independent_trial_count": 1.0,
        "validation_metrics": {"auc": 0.535},
        "selection_status": "REJECTED",
        "selection_reason": "No OOF candidate at economically nonnegative threshold.",
    }

    registry.append(record)

    saved = [json.loads(line) for line in (tmp_path / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    assert saved == [record]
    try:
        registry.append(record)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("trial registry must reject a duplicate trial id")
