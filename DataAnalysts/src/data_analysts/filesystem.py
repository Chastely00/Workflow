"""Shared filesystem operations."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from os import PathLike


_WINDOWS_TRANSIENT_REPLACE_WINERRORS = frozenset({5, 32})
_WINDOWS_REPLACE_MAX_ATTEMPTS = 4
_WINDOWS_REPLACE_RETRY_BASE_DELAY_SECONDS = 0.01


def replace_file(
    source: str | PathLike[str],
    destination: str | PathLike[str],
    *,
    operation: Callable[[str | PathLike[str], str | PathLike[str]], None] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    replace_operation = os.replace if operation is None else operation
    sleeper = time.sleep if sleep is None else sleep

    for attempt in range(_WINDOWS_REPLACE_MAX_ATTEMPTS):
        try:
            replace_operation(source, destination)
            return
        except PermissionError as exc:
            if (
                getattr(exc, "winerror", None)
                not in _WINDOWS_TRANSIENT_REPLACE_WINERRORS
                or attempt == _WINDOWS_REPLACE_MAX_ATTEMPTS - 1
            ):
                raise
            sleeper(_WINDOWS_REPLACE_RETRY_BASE_DELAY_SECONDS * (attempt + 1))
