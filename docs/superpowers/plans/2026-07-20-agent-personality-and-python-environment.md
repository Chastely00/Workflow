# Agent 個性與 Python 環境 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立明確的量化研究 Agent 工作契約，以及可在 VS Code Notebook 使用、可重建且通過實際 smoke checks 的 Python 3.12 `.venv`。

**Architecture:** 專案規範集中在 `AGENTS.md`，環境選擇與排除規則分別由 `.vscode/settings.json` 與 `.gitignore` 管理。`.venv` 是不進入 Git 的執行環境，`requirements.txt` 記錄實際解析完成的精確版本，`scripts/verify_environment.py` 以獨立檢查驗證每個直接依賴與關鍵行為。

**Tech Stack:** Windows PowerShell、CPython 3.12、venv、pip、NumPy、pandas、PyMongo、IPython kernel、PyArrow、Numba、SciPy、statsmodels、Matplotlib、seaborn、scikit-learn、TA-Lib、Python `unittest`。

## Global Constraints

- 使用 `py -3.12` 建立 `.venv`，不得使用預設的 Anaconda Python 3.10。
- 直接依賴固定為 `numpy pandas pymongo ipykernel pyarrow numba scipy statsmodels matplotlib seaborn scikit-learn TA-Lib`。
- 未提供 MongoDB URI 時使用 `mongodb://localhost:27017/`；明確提供的 URI 失敗時不得回退 localhost。
- `.venv` 不得進入 Git，也不得註冊為使用者層級的全域 Jupyter kernel。
- 禁止提交帳密、連線字串、資料集或環境產生內容。
- TA-Lib 安裝或匯入失敗時保留原始錯誤並停止，不得自行替換套件。
- 所有成功宣告必須由實際命令輸出、測試或 artifact 支持。
- 本計畫只建立本機 commit；除非使用者另行要求，不 push 至 GitHub。

## 檔案結構

- `AGENTS.md`：Agent 個性、量化研究契約、工程規範與溝通方式。
- `.gitignore`：排除虛擬環境、Python/Notebook cache、機密及本機雜項。
- `.vscode/settings.json`：讓 VS Code 優先選擇專案 `.venv`。
- `requirements.txt`：由成功安裝並驗證的 `.venv` 產生精確套件版本。
- `scripts/verify_environment.py`：逐元件執行環境 smoke checks，失敗時輸出元件名稱並回傳非零狀態。
- `tests/test_verify_environment.py`：使用標準函式庫測試驗證 runner 的成功及 fail-closed 行為。
- `README.md`：環境重建、驗證與 VS Code Notebook 使用說明。

---

### Task 1: 建立 Agent 契約與專案環境邊界

**Files:**
- Modify: `AGENTS.md`
- Create: `.gitignore`
- Create: `.vscode/settings.json`

**Interfaces:**
- Consumes: 已核准的中文設計文件。
- Produces: 後續所有研究及實作必須遵守的 repository-local contract；VS Code 使用的預設解譯器路徑。

- [ ] **Step 1: 將 `AGENTS.md` 改為完整工作契約**

```markdown
# ROLE

你是協助使用者完成量化交易 Workflow 的協作型量化研究員，同時保有嚴格研究主管的否決紀律，以及工程研究員的執行紀律。你熟悉因子分析、套利、統計檢定、資料工程與可重現研究。

## 個性與溝通

- 主要使用繁體中文，結論優先，回答簡潔且精確。
- 清楚區分「已驗證事實、推論、假設、待確認事項」。
- 不迎合不合理的方向；發現邏輯錯誤或證據不足時，直接指出原因並提出可驗證的替代方案。
- 將報酬、損益、風險與統計數據拆解至最小且仍有意義的驅動因素。
- 不以語氣、自信或命令成功結束取代 artifact、schema、計算或測試證據。

## 決策優先順序

1. 正確性
2. 無資料洩漏
3. 可重現性
4. 可解釋性
5. 效能
6. 開發便利性

## 量化研究契約

接受研究結果前，必須確認或明確標示缺失：

- 研究假設及經濟意義；
- 觀察時間、資料可得時間、訊號形成時間與實際交易時間；
- point-in-time 股票池與成分資格；
- 價格還原、公司行動、下市股票與存活者偏誤；
- 基準、交易成本、滑價、容量與週轉率；
- 樣本內、驗證集及樣本外邊界；
- 統計檢定、效果量、不確定性、多重檢定與穩健性。

發現前視偏誤、存活者偏誤、標籤洩漏、錯誤時間對齊或其他資料洩漏風險時，必須停止產出績效結論，明確說明污染路徑與需要修正的驗證方式。

## 工程規範

- 先精確定義計算，再以可人工驗算的小型案例驗證，最後才進行向量化或 JIT 優化。
- 優先使用 NumPy、pandas、SciPy、scikit-learn 或 TA-Lib 的向量化操作，但不得為速度犧牲語意正確性。
- 只優化經量測確認的瓶頸，優化後必須重新執行正確性檢查。
- 禁止靜默攔截例外、隱性資料來源 fallback、無聲刪列及會改變資料意義的 dtype 轉換。
- 只要 API 支援，涉及隨機性的研究必須設定明確 seed。
- PyMongo 未收到 URI 時使用 `mongodb://localhost:27017/`；若已明確提供其他 URI 而連線失敗，不得回退 localhost。
- 新增依賴必須是有意識的決策，安裝後更新 `requirements.txt` 並重新執行環境驗證。
```

- [ ] **Step 2: 建立 `.gitignore`**

```gitignore
# Project virtual environment
.venv/

# Python generated files
__pycache__/
*.py[cod]
*.pyd
.pytest_cache/
.mypy_cache/
.ruff_cache/
.numba_cache/

# Jupyter
.ipynb_checkpoints/

# Coverage and build outputs
.coverage
htmlcov/
build/
dist/
*.egg-info/

# Local secrets and environment variables
.env
.env.*
!.env.example
*.pem
*.key

# Local editor and OS files
.DS_Store
Thumbs.db
desktop.ini
.vscode/*
!.vscode/settings.json
```

- [ ] **Step 3: 建立 `.vscode/settings.json`**

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}\\.venv\\Scripts\\python.exe"
}
```

- [ ] **Step 4: 驗證規範內容與 ignore 規則**

Run:

```powershell
rg -n "協作型量化研究員|資料洩漏|mongodb://localhost:27017/|python.defaultInterpreterPath" AGENTS.md .vscode/settings.json
git check-ignore -v .venv\Scripts\python.exe .ipynb_checkpoints\checkpoint.ipynb __pycache__\module.pyc .env
git diff --check
```

Expected:

- `rg` 同時找到 Agent 定位、資料洩漏規則、localhost URI 與 `.venv` 解譯器設定。
- `git check-ignore` 的四個路徑全部由 `.gitignore` 規則命中。
- `git diff --check` 無錯誤。

- [ ] **Step 5: 提交 Task 1**

```powershell
git add AGENTS.md .gitignore .vscode/settings.json
git commit -m "chore: define quant research workspace contract"
```

Expected: commit 成功，且不包含 `.venv`。

---

### Task 2: 建立 Python 3.12 `.venv` 並鎖定依賴

**Files:**
- Create: `.venv/`（Git ignored）
- Create: `requirements.txt`

**Interfaces:**
- Consumes: `py -3.12`、Task 1 的 `.gitignore`。
- Produces: `C:\Users\ChastLai\Documents\量化交易Workflow\.venv\Scripts\python.exe` 與可重建的精確依賴集合。

- [ ] **Step 1: 確認基礎 Python 版本並建立環境**

Run:

```powershell
py -3.12 -c "import sys; print(sys.executable); print(sys.version)"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version; print(sys.executable); print(sys.version)"
```

Expected: 三個命令皆成功，最後的 executable 位於本專案 `.venv\Scripts\python.exe`，版本為 3.12.x。

- [ ] **Step 2: 更新環境內 pip**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip --version
```

Expected: pip 路徑位於本專案 `.venv`。

- [ ] **Step 3: 安裝核准的直接依賴**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install numpy pandas pymongo ipykernel pyarrow numba scipy statsmodels matplotlib seaborn scikit-learn TA-Lib
```

Expected: pip exit code 為 0。若 `TA-Lib` 失敗，保留完整錯誤並停止 Task 2，不得替換其他套件。

- [ ] **Step 4: 先驗證所有直接依賴可匯入**

Run:

```powershell
.\.venv\Scripts\python.exe -c "import numpy, pandas, pymongo, ipykernel, pyarrow, numba, scipy, statsmodels, matplotlib, seaborn, sklearn, talib; print('DIRECT_IMPORTS_OK')"
```

Expected: 輸出 `DIRECT_IMPORTS_OK`。

- [ ] **Step 5: 由成功環境產生精確版本鎖定**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip freeze | Sort-Object | Set-Content -LiteralPath requirements.txt -Encoding utf8
.\.venv\Scripts\python.exe -m pip check
Get-Content -LiteralPath requirements.txt
```

Expected:

- `requirements.txt` 包含核准直接依賴及其傳遞依賴的精確版本。
- `pip check` 輸出 `No broken requirements found.`。

- [ ] **Step 6: 確認 `.venv` 未進入 Git 並提交版本鎖定**

Run:

```powershell
git check-ignore -v .venv\Scripts\python.exe
git status --short
git add requirements.txt
git commit -m "build: lock quant research dependencies"
```

Expected: `.venv` 被 ignore；commit 只包含 `requirements.txt`。

---

### Task 3: 以測試驅動建立環境驗證腳本

**Files:**
- Create: `tests/test_verify_environment.py`
- Create: `scripts/verify_environment.py`

**Interfaces:**
- Consumes: Task 2 的 `.venv` 與所有核准依賴。
- Produces: `run_checks(checks: list[tuple[str, Callable[[], None]]]) -> int`、`main() -> int`，成功回傳 0；任一元件失敗時在 stderr 命名元件並回傳 1。

- [ ] **Step 1: 先建立失敗的 runner 測試**

Create `tests/test_verify_environment.py`:

```python
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
```

- [ ] **Step 2: 執行測試並確認因模組不存在而失敗**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_verify_environment -v
```

Expected: FAIL 或 ERROR，指出 `scripts.verify_environment` 尚不存在或沒有 `run_checks`。

- [ ] **Step 3: 建立最小 runner 與全部環境檢查**

Create `scripts/verify_environment.py`:

```python
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence

DEFAULT_MONGODB_URI = "mongodb://localhost:27017/"
Check = tuple[str, Callable[[], None]]


def check_python() -> None:
    assert sys.version_info[:2] == (3, 12), sys.version


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

    assert sum_of_squares(np.array([1.0, 2.0, 3.0])) == 14.0


def check_statistics() -> None:
    import numpy as np
    import statsmodels.api as sm
    from scipy import stats

    t_result = stats.ttest_1samp(np.array([1.0, 2.0, 3.0]), popmean=2.0)
    assert np.isclose(t_result.statistic, 0.0)

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
    assert prediction.shape == (1,)
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
    from pymongo.uri_parser import parse_uri

    uri = os.environ.get("MONGODB_URI", DEFAULT_MONGODB_URI)
    if uri == DEFAULT_MONGODB_URI:
        assert parse_uri(uri)["nodelist"] == [("localhost", 27017)]
    client = MongoClient(uri, connect=False, serverSelectionTimeoutMS=1000)
    try:
        assert client is not None
    finally:
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
        except Exception as exc:
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"[PASS] {name}")
    return 0


def main() -> int:
    return run_checks(CHECKS)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 執行 runner 單元測試**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_verify_environment -v
```

Expected: 2 tests PASS；失敗元件測試確認 runner 回傳 1 且 stderr 含元件名稱與原始例外。

- [ ] **Step 5: 執行完整環境 smoke checks**

Run:

```powershell
Remove-Item Env:MONGODB_URI -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe scripts\verify_environment.py
```

Expected: 依序輸出 10 個 `[PASS]`，包含 `ta_lib` 與 `pymongo`，exit code 為 0；測試過程不要求本機 MongoDB server 正在執行。

- [ ] **Step 6: 提交 Task 3**

```powershell
git add scripts/verify_environment.py tests/test_verify_environment.py
git commit -m "test: verify quant research environment"
```

Expected: commit 成功，且只包含驗證腳本與測試。

---

### Task 4: 完成使用說明與端對端驗收

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `requirements.txt`、`.venv`、`scripts/verify_environment.py`、VS Code 設定。
- Produces: 從乾淨 checkout 重建環境及選擇 Notebook kernel 的操作契約。

- [ ] **Step 1: 寫入 `README.md`**

```markdown
# 量化交易 Workflow

本專案使用 Python 3.12 的 repository-local `.venv`。預設 MongoDB URI 為 `mongodb://localhost:27017/`；若明確提供其他 URI 而連線失敗，不會自動回退 localhost。

## 建立環境

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 驗證環境

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest tests.test_verify_environment -v
.\.venv\Scripts\python.exe scripts\verify_environment.py
```

三個命令都成功後，才代表環境已完成。

## 在 VS Code 執行 Notebook

1. 安裝 Microsoft Python 與 Jupyter extensions。
2. 用 VS Code 開啟本 repository 資料夾。
3. 開啟 `.ipynb`，點選右上角 **Select Kernel**。
4. 選擇 **Python Environments**，再選 `.venv\Scripts\python.exe`。

本專案不需要執行 `ipykernel install --user`，也不會建立全域 kernel。

## 新增依賴

新增套件後必須重新執行完整驗證，再更新鎖定檔：

```powershell
.\.venv\Scripts\python.exe -m pip freeze | Sort-Object | Set-Content -LiteralPath requirements.txt -Encoding utf8
```
```

- [ ] **Step 2: 驗證 README 命令與關鍵 contract**

Run:

```powershell
rg -n "Python 3.12|mongodb://localhost:27017/|Select Kernel|ipykernel install --user|verify_environment.py" README.md
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\verify_environment.py
```

Expected:

- README 包含重建、localhost、Notebook kernel 與驗證說明。
- `pip check` 無 broken requirements。
- 2 tests PASS。
- 10 個 smoke checks PASS。

- [ ] **Step 3: 驗證版本鎖定可解析**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install --dry-run -r requirements.txt
```

Expected: 所有 requirements 均已滿足，無解析衝突。

- [ ] **Step 4: 驗證 Git artifact surface**

Run:

```powershell
git check-ignore -v .venv\Scripts\python.exe __pycache__\module.pyc .ipynb_checkpoints\checkpoint.ipynb .env
git status --short --ignored
git diff --check
```

Expected:

- `.venv`、Python cache、Notebook checkpoint 與 `.env` 均顯示 ignored。
- 不得出現 `.venv` 內檔案準備提交。
- `git diff --check` 無錯誤。

- [ ] **Step 5: 提交文件並做最終狀態檢查**

```powershell
git add README.md
git commit -m "docs: document environment and notebook workflow"
git status --short
git log --oneline --decorate -6
```

Expected: README commit 成功；working tree 無未預期變更。若只剩明確已知且屬於使用者的變更，須在交付時逐項列出，不得宣稱 clean。
