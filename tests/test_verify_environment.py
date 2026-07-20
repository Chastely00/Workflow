from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest import mock

import numpy as np

from scripts import verify_environment
from scripts.verify_environment import run_checks


class RunChecksTests(unittest.TestCase):
    def test_returns_zero_when_all_checks_pass(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = run_checks([("first", lambda: None), ("second", lambda: None)])

        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "[PASS] first\n[PASS] second\n")
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


class OptimizedRuntimeChecksTests(unittest.TestCase):
    def test_python_version_mismatch_raises_runtime_error(self) -> None:
        with mock.patch.object(verify_environment.sys, "version_info", (3, 11, 9)):
            with self.assertRaisesRegex(RuntimeError, "Python 3.12"):
                verify_environment.check_python()

    def test_numba_wrong_result_raises_runtime_error(self) -> None:
        fake_numba = ModuleType("numba")

        def fake_njit(*, cache: bool):
            self.assertFalse(cache)

            def decorate(function):
                del function
                return lambda values: 13.0

            return decorate

        fake_numba.njit = fake_njit  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"numba": fake_numba}):
            with self.assertRaisesRegex(RuntimeError, "Numba"):
                verify_environment.check_numba()

    def test_statistics_wrong_t_statistic_raises_runtime_error(self) -> None:
        with mock.patch(
            "scipy.stats.ttest_1samp",
            return_value=SimpleNamespace(statistic=1.0),
        ):
            with self.assertRaisesRegex(RuntimeError, "t-test"):
                verify_environment.check_statistics()

    def test_sklearn_wrong_shape_raises_runtime_error(self) -> None:
        class WrongShapeLinearRegression:
            def fit(self, features, target):
                del features, target
                return self

            def predict(self, features):
                del features
                return np.array([7.0, 9.0])

        with mock.patch(
            "sklearn.linear_model.LinearRegression",
            WrongShapeLinearRegression,
        ):
            with self.assertRaisesRegex(RuntimeError, "shape"):
                verify_environment.check_sklearn()

    def test_pymongo_wrong_localhost_default_raises_runtime_error(self) -> None:
        client = mock.Mock()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("pymongo.MongoClient", return_value=client),
            mock.patch(
                "pymongo.uri_parser.parse_uri",
                return_value={"nodelist": [("not-localhost", 27017)]},
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "localhost"):
                verify_environment.check_pymongo()


class PyMongoContractTests(unittest.TestCase):
    def test_default_uri_uses_localhost_without_connecting(self) -> None:
        client = mock.Mock()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("pymongo.MongoClient", return_value=client) as mongo_client,
        ):
            verify_environment.check_pymongo()

        mongo_client.assert_called_once_with(
            verify_environment.DEFAULT_MONGODB_URI,
            connect=False,
            serverSelectionTimeoutMS=1000,
        )
        client.close.assert_called_once_with()

    def test_explicit_uri_is_used_without_localhost_fallback(self) -> None:
        explicit_uri = "mongodb://db.example:27018/"
        client = mock.Mock()
        with (
            mock.patch.dict(os.environ, {"MONGODB_URI": explicit_uri}),
            mock.patch("pymongo.MongoClient", return_value=client) as mongo_client,
            mock.patch("pymongo.uri_parser.parse_uri") as parse_uri,
        ):
            verify_environment.check_pymongo()

        mongo_client.assert_called_once_with(
            explicit_uri,
            connect=False,
            serverSelectionTimeoutMS=1000,
        )
        parse_uri.assert_not_called()
        client.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
