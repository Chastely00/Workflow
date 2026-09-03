import pandas as pd

from etf_tricks.tier1.artifact import write_target_artifact


def test_target_artifact_writes_manifest_and_refuses_overwrite(tmp_path) -> None:
    output = tmp_path / "tier1"
    result = write_target_artifact(pd.DataFrame({"event_id": ["x"], "y_direction": [1]}), output, {"input_hash": "abc"})
    assert (output / "targets.parquet").exists()
    assert result["tables"]["targets"]["sha256"]
    try:
        write_target_artifact(pd.DataFrame({"event_id": ["x"], "y_direction": [1]}), output, {})
    except FileExistsError:
        pass
    else:
        raise AssertionError("overwrite must fail")
