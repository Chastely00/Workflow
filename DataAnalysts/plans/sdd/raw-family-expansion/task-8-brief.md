## Task 8: Contracts and Reader Documentation

**Files:**
- Modify: `contracts/OUTPUT_CONTRACT.md`
- Modify: `contracts/VERIFICATION_CONTRACT.md`
- Modify: `README.md`

**Boundary:**
- This task updates documentation only.
- It must not change code behavior.

**Interfaces:**
- Documents artifact paths, schemas, and verification metrics produced by Tasks 1-7.

- [ ] **Step 1: Update `OUTPUT_CONTRACT.md` raw family section**

Add:

```markdown
## Raw Family Expansion Outputs

Raw family artifacts are registry-driven canonical parquet surfaces. They are not feature tables and they are not strategy inputs until Feature Analysts consume them.

Required raw outputs:

| artifact_id | layer | partitioning | PIT field |
|---|---|---|---|
| trading_calendar | raw | single_file | zdate |
| daily_tradability | raw | year | mdate |
| daily_chip | raw | year | mdate |
| monthly_sales | raw | available_year | annd_s |
| financial_statement_raw | raw | available_year | key3 |
| financial_statement_pit_selected | derived | decision_year | source_available_date |
| self_reported_numbers_raw | raw | available_year | annd |
| self_reported_numbers_pit_selected | derived | decision_year | source_available_date |
| taiwan_index_futures_near_month | raw | year | 日期 |

Governance and event raw families use `mdate` as `source_available_date` and publish by `available_year`.
```

- [ ] **Step 2: Update `VERIFICATION_CONTRACT.md` raw thresholds**

Add:

```markdown
## Raw Family Thresholds

Verification blocks unless:

- `pit_parse_failure_count_total == 0`
- `unresolved_duplicate_count_total == 0`
- `forbidden_source_usage_count_total == 0`
- every manifest artifact path stays under DataAnalysts root
- every selected PIT view has `source_available_date <= decision_date`

Every raw family diagnostic must report:

- `source_row_count`
- `published_row_count`
- `omitted_row_count`
- `pit_null_count`
- `pit_parse_failure_count`
- `duplicate_logical_key_count`
- `resolved_duplicate_count`
- `unresolved_duplicate_count`
- `date_min`
- `date_max`
- `artifact_file_count`
```

- [ ] **Step 3: Update `README.md` source coverage**

Add a short section:

```markdown
## Raw Family Coverage

Raw Family Expansion publishes trading calendar, daily tradability, daily chip, monthly sales, financial statements from `TEJ.AINVFINB`, self-reported numbers from `TEJ.AFESTM1`, governance/event tables, and TX near-month futures. `TEJ.AINVFQ1` and `TEJ.APISHRACTW` are forbidden and fail verification.
```

- [ ] **Step 4: Verify docs**

Run:

```powershell
rg -n "Raw Family Expansion|AINVFQ1|APISHRACTW|AINVFINB|AFESTM1|pit_parse_failure_count_total" README.md contracts
```

Expected: command exits `0` and prints the updated sections.

---

