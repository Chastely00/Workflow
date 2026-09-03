import os
from pathlib import Path

import pytest

import data_analysts.run_transaction as transaction_module
from data_analysts.paths import DataAnalystsContext
from data_analysts.run_transaction import FormalStoreRollbackError, FormalStoreTransaction


def test_rollback_failure_preserves_backup_and_reports_unrestored_target(
    tmp_path, monkeypatch
):
    context = DataAnalystsContext.from_paths(tmp_path)
    target = context.store_path("metadata", "state.json")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"before")
    transaction = FormalStoreTransaction(context)

    with pytest.raises(FormalStoreRollbackError) as captured:
        with transaction:
            staging = target.with_name(".state.changed")
            staging.write_bytes(b"after")
            os.replace(staging, target)
            real_replace = transaction_module.os.replace

            def fail_restore(source: Path, destination: Path) -> None:
                if Path(destination) == target:
                    raise PermissionError("synthetic restore lock")
                real_replace(source, destination)

            monkeypatch.setattr(transaction_module.os, "replace", fail_restore)
            raise ValueError("force rollback")

    assert target.read_bytes() == b"after"
    assert transaction.recovery_path is not None
    recovery_path = Path(transaction.recovery_path)
    assert recovery_path.exists()
    assert (recovery_path / "metadata" / "state.json").read_bytes() == b"before"
    assert target.as_posix() in captured.value.unrestored_targets
    assert str(recovery_path) in str(captured.value)
