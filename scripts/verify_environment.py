from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence

DEFAULT_MONGODB_URI = "mongodb://localhost:27017/"
Check = tuple[str, Callable[[], None]]


def check_python() -> None:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Expected Python 3.12, got {sys.version}")


def check_direct_imports() -> None:
    import ipykernel  # noqa: F401
    import matplotlib  # noqa: F401
    import numba  # noqa: F401
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401
    import pymongo  # noqa: F401
    import scipy  # noqa: F401
    import seaborn  # noqa: F401
    import sklearn  # noqa: F401
    import statsmodels  # noqa: F401
    import talib  # noqa: F401


def check_numpy_pandas() -> None:
    import numpy as np
    import pandas as pd

    frame = pd.DataFrame({"value": np.array([1.0, 2.0, 3.0])})
    result = frame["value"].to_numpy() ** 2
    np.testing.assert_allclose(result, np.array([1.0, 4.0, 9.0]))


def check_pyarrow() -> None:
    import pandas as pd
    import pyarrow as pa

    original = pd.DataFrame({"symbol": ["2330", "2317"], "value": [1.5, 2.5]})
    restored = pa.Table.from_pandas(original, preserve_index=False).to_pandas()
    pd.testing.assert_frame_equal(restored, original)


def check_numba() -> None:
    import numpy as np
    from numba import njit

    @njit(cache=False)
    def sum_of_squares(values: np.ndarray) -> float:
        return float((values * values).sum())

    result = sum_of_squares(np.array([1.0, 2.0, 3.0]))
    if result != 14.0:
        raise RuntimeError(f"Numba sum-of-squares expected 14.0, got {result!r}")


def check_statistics() -> None:
    import numpy as np
    import statsmodels.api as sm
    from scipy import stats

    t_result = stats.ttest_1samp(np.array([1.0, 2.0, 3.0]), popmean=2.0)
    if not np.isclose(t_result.statistic, 0.0):
        raise RuntimeError(
            f"SciPy t-test expected statistic 0.0, got {t_result.statistic!r}"
        )

    x = sm.add_constant(np.array([0.0, 1.0, 2.0, 3.0]))
    y = np.array([1.0, 3.0, 5.0, 7.0])
    fitted = sm.OLS(y, x).fit()
    np.testing.assert_allclose(fitted.params, np.array([1.0, 2.0]), atol=1e-12)


def check_plotting() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    figure, axis = plt.subplots()
    sns.lineplot(x=[1, 2, 3], y=[1, 4, 9], ax=axis)
    figure.canvas.draw()
    plt.close(figure)


def check_sklearn() -> None:
    import numpy as np
    from sklearn.linear_model import LinearRegression

    model = LinearRegression().fit(
        np.array([[0.0], [1.0], [2.0]]),
        np.array([1.0, 3.0, 5.0]),
    )
    prediction = model.predict(np.array([[3.0]]))
    if prediction.shape != (1,):
        raise RuntimeError(
            f"scikit-learn prediction expected shape (1,), got {prediction.shape!r}"
        )
    np.testing.assert_allclose(prediction, np.array([7.0]), atol=1e-12)


def check_talib() -> None:
    import numpy as np
    import talib

    result = talib.SMA(np.array([1.0, 2.0, 3.0, 4.0]), timeperiod=3)
    np.testing.assert_allclose(
        result,
        np.array([np.nan, np.nan, 2.0, 3.0]),
        equal_nan=True,
    )


def check_pymongo() -> None:
    from pymongo import MongoClient
    from pymongo.uri_parser import parse_uri, split_options

    uri = os.environ.get("MONGODB_URI", DEFAULT_MONGODB_URI)
    if uri.startswith("mongodb+srv://"):
        _, has_query, query = uri.partition("?")
        if has_query:
            split_options(query, warn=False)
        client = MongoClient(
            uri,
            connect=False,
            serverSelectionTimeoutMS=1000,
        )
    else:
        parsed_uri = parse_uri(uri, warn=False)
        if uri == DEFAULT_MONGODB_URI:
            nodelist = parsed_uri["nodelist"]
            if nodelist != [("localhost", 27017)]:
                raise RuntimeError(
                    "PyMongo default URI expected localhost:27017, "
                    f"got {nodelist!r}"
                )
        client = MongoClient(uri, connect=False, serverSelectionTimeoutMS=1000)

    if client is None:
        raise RuntimeError("PyMongo client construction returned None")
    client.close()


CHECKS: list[Check] = [
    ("python", check_python),
    ("direct_imports", check_direct_imports),
    ("numpy_pandas", check_numpy_pandas),
    ("pyarrow", check_pyarrow),
    ("numba", check_numba),
    ("statistics", check_statistics),
    ("plotting", check_plotting),
    ("scikit_learn", check_sklearn),
    ("ta_lib", check_talib),
    ("pymongo", check_pymongo),
]


def run_checks(checks: Sequence[Check]) -> int:
    for name, check in checks:
        try:
            check()
        except (Exception, SystemExit) as exc:
            if name == "pymongo":
                print(f"[FAIL] {name}: {type(exc).__name__}", file=sys.stderr)
            else:
                print(f"[FAIL] {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"[PASS] {name}")
    return 0


def main() -> int:
    return run_checks(CHECKS)


if __name__ == "__main__":
    raise SystemExit(main())
