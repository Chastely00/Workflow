# Config Contract

DataAnalysts 的操作參數主要由 `project_root/configs` 管理。CLI 只保留正式資料產品需要的最小參數，避免把資料語意變成人工選項。

## Config Layout

正式 configs 由 `project_root/configs` 載入：

```text
project_root/configs/
  mongodb_sources.json
  source_family_profiles.json
  universe_specs.json
  source_catalog.json
  pit_registry.json
  artifact_contracts.json
```

正式 metadata snapshot 位於：

```text
data_store/metadata/config_snapshot/
  mongodb_sources.json
  source_family_profiles.json
  universe_specs.json
  source_catalog.json
  pit_registry.json
  artifact_contracts.json
```

規則：

- configs 載入來源固定為 `project_root/configs`。
- config snapshots 必須來自本次執行實際使用的 `project_root/configs`。
- snapshots 必須可由 `data_store/metadata/data_store_manifest.json` 的 `config_hashes` 重算驗證。
- config 與 snapshot 都不得包含 secret、token、個人帳號或遠端 MongoDB URI。
- 唯一允許寫入 config 的 URI 是無帳密 localhost default：`mongodb://localhost:27017/`。

## `mongodb_sources.json`

目的：

- 定義 MongoDB connection 的環境變數名稱。
- 定義 database 與 collection namespace。
- 不存放 credential。

規則：

- `uri_env` 若存在於執行環境，優先使用環境變數。
- `uri_env` 不存在時，使用 `default_uri`。
- `default_uri` 只能是無帳密 localhost。
- config 與 config snapshot 都不得提供遠端 host、帳密、token 或個人帳號形式的 plaintext URI。

## `source_family_profiles.json`

目的：

- 定義每個 source family 如何抽取、PIT normalization、partition、publish。

允許的 `source_profile`：

```text
small_snapshot
medium_pit_table
large_daily_panel
```

規則：

- `family_id` 必須唯一。
- PIT table 必須定義 availability rule。
- large daily panel 必須有 bounded query rule。
- unknown `source_profile` 必須 blocked。

## `universe_specs.json`

目的：

- 定義 DataAnalysts-owned universe selector。
- selector 只可使用 security panel 欄位。
- historical-safe selector 不得引用 realized/forward outcome。

## `artifact_contracts.json`

目的：

- 集中定義正式 artifact 的 path、required columns、logical key、partition/date/availability fields、PIT policy、source families 與 publication mode。
- 將每個 universe 展開成 `historical` 與 `exact_date` 兩個獨立 contract/manifest identity。

規則：

- `pipeline.py` 不得自行組合 canonical 路徑。
- 只允許 `full_replace`、`partition_upsert`、`snapshot_by_value`。
- `allow_empty` 必須是 explicit boolean，預設 `false`；只有來源 domain 合法可能為空的 artifact 才可設為 `true`。
- path 必須是安全的 data-store-relative path，禁止 absolute、parent traversal 與未展開 template variable。
- raw contract logical key 必須與 `source_family_profiles.json` primary key 一致。
- 新增 enabled family 或 universe variant 時，registry coverage 不完整必須 fail closed。

## Config Validation

每次 run 前必須先驗證：

- required config file exists。
- artifact registry 完整且與 enabled source families / universe specs 一致。
- `schema_version` supported。
- family ids unique。
- enabled family has source definition。
- required daily family enabled or explicitly waived by config。
- universe selector fields all belong to security panel schema。
- universe filter operators are limited to `eq`、`gte`、`not_null`。
- configs 不含 secret、token、個人帳號或遠端 MongoDB URI。

驗證失敗時必須 fail closed，並寫入：

```text
data_store/jobs/config_validation_result.json
```
