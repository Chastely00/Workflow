# ETF Tricks AFML Notebook Quickstart

Notebook 應建立／保留在 repository root：

`C:\Users\ChastLai\Documents\量化交易Workflow\ETF_Tricks_AFML_Quickstart.ipynb`

在這個位置啟動 Jupyter，repository package 可直接用 `import etf_tricks` 載入，不需要修改 `sys.path`。Kernel 必須選：

`C:\Users\ChastLai\Documents\量化交易Workflow\.venv\Scripts\python.exe`

## 最短使用流程

1. 將 VS Code/Jupyter 工作目錄設為 repository root。
2. 開啟 `ETF_Tricks_AFML_Quickstart.ipynb`。
3. 確認第一個 code cell 的 `RESULT_DIR`、`AFML_DATASET_DIR`、`DATA_ANALYSTS_ROOT` 與研究邊界。
4. 逐格執行。若 AFML manifest 已存在會驗證 hash 後讀取；否則才執行 bounded build。

目前 canonical schema 為 `etf-afml-dataset-v2`。新建資料會先完成記憶體內 core checks，再原子寫入並讀回驗證 table hash、schema、dtype、key 與 PIT cross-table clocks；只有讀回成功的 artifact 才會標示 finalized READY。舊版 v1 artifact 必須重建，不會被靜默升級。

預設上游結果路徑：

`.artifacts/etf_tricks/performance/optimized-final-20240101-20260707`

可用以下環境變數覆寫，而不必改 Notebook：

- `ETF_TRICK_RESULT_DIR`
- `ETF_AFML_DATASET_DIR`
- `DATA_ANALYSTS_ROOT`
- `ETF_AFML_TRAIN_START`
- `ETF_AFML_TRAIN_END`
- `ETF_AFML_VALIDATION_END`
- `ETF_AFML_TEST_END`
- `ETF_AFML_AS_OF`

## 主要資料介面

```python
dataset.dollar_bars
dataset.ffd_search
dataset.ffd_series
dataset.structural_features
dataset.features
dataset.events
dataset.labels
dataset.diagnostics

train = dataset.for_ml("momentum", split="train")
snapshot = dataset.for_trading(as_of="2026-07-07", decision_cutoff="after_close")
```

`for_ml()` 不會默默刪除 feature 缺值列，label 只有在 `t1` 與 `label_available_at` 都通過 split cutoff 時才會接入。`for_trading()` 不會載入或輸出 `label`、`t1`、future touch path；它只回傳截至 decision time 已可得的最後一根 live-eligible feature bar 與最早可執行交易日。

本層不負責把 ETF 資金拆成股票股數。未來模型分配資金後，應把 `etf_id`、資金與 `earliest_execution_session` 交回既有 allocation/execution API，使用該 session 的原始未還原 `close`、至少一股及最低手續費規則計算。

## 測試順序

開發與人工驗證先用 2024–2026。只有 Dollar bar、FFD 或 rolling window 觀察數不足，才延伸至 2020–2026並記錄原因。13 ETF 完整歷史只能在所有 bounded gates 通過後，以 `full_history_acceptance=True` 明確啟動一次驗收。
