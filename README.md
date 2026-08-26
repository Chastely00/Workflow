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

提交 `.ipynb` 前，必須清除 cell output，並人工檢查不得殘留帳密、連線字串、token、查詢結果或其他敏感資料。

## ETF Tricks Notebook

[`scripts/etf_tricks_quickstart.ipynb`](scripts/etf_tricks_quickstart.ipynb) 是唯一建議的 Notebook 起點。核心公式位於 `etf_tricks` package，Notebook 只負責設定參數與檢視輸出。

```python
from etf_tricks import ETFTrickLab

lab = ETFTrickLab.from_data_analysts("DataAnalysts")
result = lab.run_all(
    start_date="2026-07-01",
    end_date="2026-07-07",
    initial_capital=10_000_000,
)

result.nav
result.amount
result.for_ffd("momentum")
lab.validate(result)
```

已存在的 finalized artifact 可直接重新載入，不必每次重算完整歷史：

```python
from etf_tricks import ETFTrickResult

result = ETFTrickResult.read(".artifacts/etf_tricks/full-history-20050103-20260707-v3")
```

`result` 同時提供 `holdings`、`trades`、`targets`、`candidates` 與 `diagnostics`。`lab.allocate(...)` 和 `lab.rebalance(...)` 接受任意資金，回傳實際整股、費稅、剩餘現金與依下一個月 `TRADEDAY_TWSE` 展開的逐日 schedule。NT$10,000,000 只是預設驗證本金，並非固定配置。

目前範圍只建立 13 條 Daily NAV 與 ETF 成交金額曲線；`for_ffd()` 只輸出 `date, etf_id, nav, daily_return, etf_amount`，不執行 Dollar bar、FFD、ADF 或 ML。

## 新增依賴

新增套件後必須重新執行完整驗證，再更新鎖定檔：

```powershell
.\.venv\Scripts\python.exe -m pip freeze | Sort-Object | Set-Content -LiteralPath requirements.txt -Encoding utf8
```
