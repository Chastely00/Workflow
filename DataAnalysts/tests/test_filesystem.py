from pathlib import Path

import pytest

from data_analysts import filesystem


def _permission_error(winerror: int | None) -> PermissionError:
    error = PermissionError(13, "injected replace failure")
    error.winerror = winerror
    return error


@pytest.mark.parametrize("winerror", [5, 32])
def test_replace_file_retries_transient_windows_permission_error_until_success(
    winerror,
):
    source = Path("source.tmp")
    destination = Path("destination.json")
    attempts: list[tuple[Path, Path]] = []
    sleeps: list[float] = []

    def operation(actual_source: Path, actual_destination: Path) -> None:
        attempts.append((actual_source, actual_destination))
        if len(attempts) < 3:
            raise _permission_error(winerror)

    filesystem.replace_file(
        source,
        destination,
        operation=operation,
        sleep=sleeps.append,
    )

    assert attempts == [(source, destination)] * 3
    assert sleeps == pytest.approx([0.01, 0.02])


def test_replace_file_reraises_original_transient_error_after_bounded_attempts():
    failure = _permission_error(5)
    attempts = 0
    sleeps: list[float] = []

    def operation(_source: Path, _destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise failure

    with pytest.raises(PermissionError) as exc_info:
        filesystem.replace_file(
            Path("source.tmp"),
            Path("destination.json"),
            operation=operation,
            sleep=sleeps.append,
        )

    assert exc_info.value is failure
    assert attempts == 4
    assert sleeps == pytest.approx([0.01, 0.02, 0.03])
    assert sum(sleeps) == pytest.approx(0.06)


@pytest.mark.parametrize("winerror", [None, 33])
def test_replace_file_does_not_retry_other_permission_errors(winerror):
    failure = _permission_error(winerror)
    attempts = 0
    sleeps: list[float] = []

    def operation(_source: Path, _destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise failure

    with pytest.raises(PermissionError) as exc_info:
        filesystem.replace_file(
            Path("source.tmp"),
            Path("destination.json"),
            operation=operation,
            sleep=sleeps.append,
        )

    assert exc_info.value is failure
    assert attempts == 1
    assert sleeps == []


@pytest.mark.parametrize("failure", [OSError("replace failed"), RuntimeError("boom")])
def test_replace_file_does_not_retry_other_exceptions(failure):
    failure.winerror = 5
    attempts = 0
    sleeps: list[float] = []

    def operation(_source: Path, _destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise failure

    with pytest.raises(type(failure)) as exc_info:
        filesystem.replace_file(
            Path("source.tmp"),
            Path("destination.json"),
            operation=operation,
            sleep=sleeps.append,
        )

    assert exc_info.value is failure
    assert attempts == 1
    assert sleeps == []
