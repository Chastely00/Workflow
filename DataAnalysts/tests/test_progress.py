import json

import data_analysts.filesystem as filesystem_module
import data_analysts.progress as progress_module


def test_progress_atomic_write_retries_transient_windows_replace_lock(
    tmp_path, monkeypatch
):
    target = tmp_path / "jobs" / "current_run.json"
    real_replace = filesystem_module.os.replace
    attempts = 0

    def transient_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = PermissionError("synthetic transient Windows lock")
            error.winerror = 5
            raise error
        real_replace(source, destination)

    monkeypatch.setattr(filesystem_module.os, "replace", transient_replace)
    monkeypatch.setattr(filesystem_module.time, "sleep", lambda seconds: None)

    progress_module._atomic_write_json(target, {"status": "running"})

    assert attempts == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "running"}
