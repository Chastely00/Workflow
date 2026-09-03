# DataAnalysts Private Memory

## 目的

DataAnalysts private memory 只保存資料產品治理所需的長期筆記。它不保存策略績效、不保存回測訊號、不替 FeatureAnalysts 或 Strategists 做研究判斷。

## 可記於 private memory

- source schema notes。
- normalization rule。
- PIT policy detail。
- manifest generation note。
- diagnostics finding。
- verification finding。
- blocked reason detail。

## 職責內可接觸，但不可外流

- raw database rows 不得進 shared state。
- MongoDB credentials、tokens、private URI 不得進 shared state 或 private memory。
- 未經 manifest 管理的 internal paths 不得提交給 downstream agents。

## 可提交給 CIO 的 handoff

- manifest pointer。
- artifact ID。
- readiness status。
- PIT policy summary。
- coverage summary。
- diagnostics summary。
- verification result pointer。
- blocked reason category。

## 不可接收 / 不可讀 / 不可記 / 不可回傳

- downstream strategy performance feedback。
- 因回測績效要求調整 data product 的指示。
- 直接將 raw data rows 作為 handoff 提交給 FeatureAnalysts；只能提交 manifest pointer 或 artifact pointer。

## Contamination 規則

若收到 strategy performance、backtest result、調參要求、因績效要求修改 data product 的指示，或被要求將 raw data rows 提交給 FeatureAnalysts，必須停止並回報 CIO contamination。

## 未來可建立的記憶檔案

以下檔案只在有真實內容且經 CIO 核准時建立；本階段不建立空檔：

```text
DataAnalysts/memory/source_schema_notes.md
DataAnalysts/memory/pit_policy_notes.md
DataAnalysts/memory/verification_findings.md
DataAnalysts/memory/blocked_reasons.md
```
