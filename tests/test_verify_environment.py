from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest import mock

import numpy as np
from pymongo.errors import ConfigurationError, InvalidURI

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
        sentinel_ran = False

        def fail() -> None:
            raise RuntimeError("boom")

        def sentinel() -> None:
            nonlocal sentinel_ran
            sentinel_ran = True

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = run_checks(
                [("broken_component", fail), ("must_not_run", sentinel)]
            )

        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "[FAIL] broken_component: RuntimeError: boom\n",
        )
        self.assertFalse(sentinel_ran)

    def test_system_exit_zero_is_a_named_failure_and_short_circuits(self) -> None:
        sentinel_ran = False

        def exit_successfully() -> None:
            raise SystemExit(0)

        def sentinel() -> None:
            nonlocal sentinel_ran
            sentinel_ran = True

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                result = run_checks(
                    [
                        ("exiting_component", exit_successfully),
                        ("must_not_run", sentinel),
                    ]
                )
            except SystemExit as exc:
                self.fail(f"run_checks propagated SystemExit({exc.code!r})")

        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "[FAIL] exiting_component: SystemExit: 0\n",
        )
        self.assertFalse(sentinel_ran)

    def test_keyboard_interrupt_propagates(self) -> None:
        def interrupt() -> None:
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            run_checks([("interrupting_component", interrupt)])


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
            ) as parse_uri,
        ):
            with self.assertRaisesRegex(RuntimeError, "localhost"):
                verify_environment.check_pymongo()

        parse_uri.assert_called_once_with(
            verify_environment.DEFAULT_MONGODB_URI,
            warn=False,
        )
        client.close.assert_not_called()


class PyMongoContractTests(unittest.TestCase):
    def test_valid_srv_uri_constructs_client_without_dns_lookup(self) -> None:
        srv_uri = "mongodb+srv://cluster.example.com/?retryWrites=true"
        with (
            mock.patch.dict(os.environ, {"MONGODB_URI": srv_uri}),
            mock.patch(
                "pymongo.synchronous.srv_resolver._SrvResolver.get_hosts",
                side_effect=AssertionError("DNS lookup attempted"),
            ) as get_hosts,
        ):
            verify_environment.check_pymongo()

        get_hosts.assert_not_called()

    def test_invalid_srv_option_fails_closed_without_dns_lookup(self) -> None:
        invalid_srv_uri = (
            "mongodb+srv://cluster.example.com/?notARealOption=true"
        )
        with (
            mock.patch.dict(os.environ, {"MONGODB_URI": invalid_srv_uri}),
            mock.patch(
                "pymongo.synchronous.srv_resolver._SrvResolver.get_hosts",
                side_effect=AssertionError("DNS lookup attempted"),
            ) as get_hosts,
        ):
            with self.assertRaisesRegex(
                (ConfigurationError, UserWarning),
                "Unknown option",
            ):
                verify_environment.check_pymongo()

        get_hosts.assert_not_called()

    def test_default_uri_uses_localhost_without_connecting(self) -> None:
        client = mock.Mock()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("pymongo.MongoClient", return_value=client) as mongo_client,
            mock.patch(
                "pymongo.uri_parser.parse_uri",
                return_value={"nodelist": [("localhost", 27017)]},
            ) as parse_uri,
        ):
            verify_environment.check_pymongo()

        parse_uri.assert_called_once_with(
            verify_environment.DEFAULT_MONGODB_URI,
            warn=False,
        )
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

        parse_uri.assert_called_once_with(explicit_uri, warn=False)
        mongo_client.assert_called_once_with(
            explicit_uri,
            connect=False,
            serverSelectionTimeoutMS=1000,
        )
        client.close.assert_called_once_with()

    def test_explicit_uri_client_error_propagates_without_localhost_fallback(
        self,
    ) -> None:
        explicit_uri = "mongodb://db.example:27018/"
        sentinel_error = RuntimeError("explicit URI construction failed")
        with (
            mock.patch.dict(os.environ, {"MONGODB_URI": explicit_uri}),
            mock.patch(
                "pymongo.MongoClient",
                side_effect=sentinel_error,
            ) as mongo_client,
            mock.patch("pymongo.uri_parser.parse_uri") as parse_uri,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "explicit URI construction failed",
            ) as caught:
                verify_environment.check_pymongo()

        self.assertIs(caught.exception, sentinel_error)
        parse_uri.assert_called_once_with(explicit_uri, warn=False)
        mongo_client.assert_called_once_with(
            explicit_uri,
            connect=False,
            serverSelectionTimeoutMS=1000,
        )

    def test_invalid_tls_option_fails_before_client_construction(self) -> None:
        invalid_uri = "mongodb://localhost:27017/?tls=NOTREAL"
        with (
            mock.patch.dict(os.environ, {"MONGODB_URI": invalid_uri}),
            mock.patch("pymongo.MongoClient") as mongo_client,
        ):
            with self.assertRaisesRegex(ValueError, "tls"):
                verify_environment.check_pymongo()

        mongo_client.assert_not_called()

    def test_invalid_uri_format_fails_before_client_construction(self) -> None:
        invalid_uri = "not-a-mongodb-uri"
        with (
            mock.patch.dict(os.environ, {"MONGODB_URI": invalid_uri}),
            mock.patch("pymongo.MongoClient") as mongo_client,
        ):
            with self.assertRaisesRegex(InvalidURI, "Invalid URI scheme"):
                verify_environment.check_pymongo()

        mongo_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
