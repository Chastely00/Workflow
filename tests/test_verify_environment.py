from __future__ import annotations

import contextlib
import io
import unittest

from scripts.verify_environment import run_checks


class RunChecksTests(unittest.TestCase):
    def test_returns_zero_when_all_checks_pass(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = run_checks([("first", lambda: None), ("second", lambda: None)])

        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")

    def test_fails_closed_and_names_component(self) -> None:
        def fail() -> None:
            raise RuntimeError("boom")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = run_checks([("broken_component", fail)])

        self.assertEqual(result, 1)
        self.assertIn("broken_component", stderr.getvalue())
        self.assertIn("RuntimeError: boom", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
