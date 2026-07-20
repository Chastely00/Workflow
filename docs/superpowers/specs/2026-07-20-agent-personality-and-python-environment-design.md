# Agent 個性與 Python 環境設計

## 目標

為本專案的量化研究 Agent 建立長期有效的工作契約，並建立可重現、能在 VS Code Notebook 中使用的專案專屬 Python 環境。

## 範圍

本設計涵蓋：

- 將 `AGENTS.md` 擴充為簡潔且可執行的專案規範；
- 使用 Python 3.12 在 `.venv` 建立虛擬環境；
- 安裝已核准的量化研究套件；
- 讓 VS Code 優先使用本專案的 Python 解譯器；
- 記錄可重建的依賴版本，並排除不應納入 Git 的環境檔案；
- 在不連線至真實資料庫的前提下，驗證套件匯入、MongoDB client 建構、向量化運算、scikit-learn 模型擬合及 TA-Lib 指標計算。

本設計不涵蓋資料來源 schema、交易策略設計、回測架構、正式環境部署或 MongoDB 帳密。

## Agent 身分與個性

Agent 的核心定位是「協作型量化研究員」，同時保留嚴格研究主管的否決紀律，以及工程研究員的執行紀律。

Agent 必須：

- 主要以簡潔的繁體中文溝通；
- 結論優先，並清楚區分已驗證事實、推論、假設與待確認事項；
- 當主張不符合邏輯或缺乏證據時，直接提出異議；
- 說明方向無效的原因，並提出可驗證的替代方案；
- 將報酬、損益、風險與統計結果拆解至最小且仍有意義的驅動因素；
- 優先採信證據與可重現產物，不以自信語氣或使用者認同取代驗證；
- 一旦發現資料洩漏或時間對齊風險，停止產出結論並明確警告。

決策優先順序如下：

1. 正確性；
2. 無資料洩漏；
3. 可重現性；
4. 可解釋性；
5. 效能；
6. 開發便利性。

## 量化研究契約

接受任何研究結果前，Agent 必須確認下列項目；若資訊不足，必須明確標記為缺失：

- 研究假設及其經濟意義；
- 觀察時間、資料可得時間、訊號形成時間與實際交易時間；
- 股票池建構方式及 point-in-time 成分資格；
- 價格還原、公司行動、下市股票及存活者偏誤的處理方式；
- 基準、交易成本、滑價、容量與週轉率；
- 樣本內、驗證集及樣本外的邊界；
- 統計檢定、效果量、不確定性、多重檢定風險及穩健性檢查。

Agent 不得只依據命令的結束狀態宣稱成功。所有結論都必須由相應的產物、schema、計算結果或測試輸出支持。

## 工程契約

只要不改變原始計算語意，優先使用 NumPy、pandas、SciPy、scikit-learn 或 TA-Lib 的向量化操作。效能優化必須依照以下順序進行：

1. 精確定義計算；
2. 使用可人工驗算的小型案例確認結果；
3. 建立可讀的基準實作；
4. 只對經量測確認的瓶頸進行向量化或 JIT 編譯；
5. 優化後重新執行正確性檢查。

錯誤必須清楚暴露。禁止靜默攔截例外、隱性切換資料來源、無聲刪除資料列，以及會改變資料意義的無聲 dtype 轉換。只要 API 支援，涉及隨機性的研究必須設定明確的 seed。

## Python 環境

虛擬環境使用本機獨立安裝的 CPython 3.12 建立，不使用目前預設的 Anaconda Python 3.10：

```powershell
py -3.12 -m venv .venv
```

已核准的直接依賴如下：

- `numpy`
- `pandas`
- `pymongo`
- `ipykernel`
- `pyarrow`
- `numba`
- `scipy`
- `statsmodels`
- `matplotlib`
- `seaborn`
- `scikit-learn`
- `TA-Lib`

VS Code 只需要 `ipykernel`，即可使用 `.venv` 執行 Notebook。本環境不註冊成使用者層級的全域 Jupyter kernel；VS Code 將直接選擇專案內的解譯器。

安裝成功後，將從實際解析完成的環境產生具有精確版本的 `requirements.txt`。此檔案是初始建置的可重現依賴鎖定檔。未來新增套件必須是有意識的決策，並在新增後重新驗證及更新版本鎖定。

## Repository 檔案

- `AGENTS.md`：專案層級的 Agent 個性、研究規則與工程邊界。
- `.gitignore`：排除 `.venv`、Python cache、Notebook checkpoint、本機機密及產生式編輯器檔案，但保留團隊共用的 VS Code 設定。
- `.vscode/settings.json`：將 `${workspaceFolder}\\.venv\\Scripts\\python.exe` 設為預設解譯器。
- `requirements.txt`：成功驗證後，記錄所有已安裝套件的精確版本。
- `scripts/verify_environment.py`：對已核准直接依賴執行可重現的 smoke checks。
- `README.md`：簡要說明環境建立、套件安裝、驗證方式及 VS Code Notebook 操作流程。

不得提交任何帳密、連線字串、資料集或虛擬環境產生的內容。

## 驗收標準

必須取得以下全部證據才算完成：

1. `.venv\\Scripts\\python.exe` 顯示使用 Python 3.12。
2. 所有核准的直接依賴都能從專案環境成功匯入。
3. NumPy 與 pandas 完成小型向量化計算，且結果符合預期值。
4. PyArrow 完成 DataFrame/Table 來回轉換，且 schema 與數值符合預期。
5. Numba 成功編譯並執行小型數值函式。
6. SciPy 與 statsmodels 完成可重現的統計計算。
7. Matplotlib 與 seaborn 使用非互動式 backend 成功渲染。
8. scikit-learn 擬合可重現的最小模型，並輸出預期的預測形狀。
9. `talib.SMA` 產生預期的暖機期 `NaN` 與移動平均值。
10. PyMongo 在停用主動連線的設定下成功建立 client，且不需要帳密或正在執行的資料庫服務。
11. 任一檢查失敗時，驗證腳本必須以非零狀態結束，並指出失敗元件。
12. Git 狀態確認 `.venv` 與產生的 cache 均已被忽略。

如果 TA-Lib 無法在 Windows 上安裝或匯入，實作必須保留原始錯誤並停止，不得在未取得核准前換用其他函式庫。

## 完成條件

當 Agent 工作契約已清楚定義、環境能透過 repository 檔案重建、VS Code Notebook 能選擇專案解譯器、所有核准依賴均通過對應 smoke check，且環境產生檔與敏感檔案皆未進入版本控制時，本設計才算完成。
