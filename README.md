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

## 新增依賴

新增套件後必須重新執行完整驗證，再更新鎖定檔：

```powershell
.\.venv\Scripts\python.exe -m pip freeze | Sort-Object | Set-Content -LiteralPath requirements.txt -Encoding utf8
```
