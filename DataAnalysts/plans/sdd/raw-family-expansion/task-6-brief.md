## Task 6: Governance/Event and Futures Raw Families

**Files:**
- Modify: `src/data_analysts/raw_families.py`
- Modify: `tests/test_raw_family_normalization.py`
- Modify: `tests/test_raw_family_pipeline.py`

**Boundary:**
- This task handles generic `mdate` governance/event tables and `Futures_TAIFEX_TX.TX_1`.
- It must not include `AINVFQ1`, `APISHRACTW`, `AINVFINB`, or `AFESTM1` in generic governance logic.

**Interfaces:**
- Produces raw canonical artifacts for:
  - `director_supervisor_holdings`
  - `board_reelection_statistics`
  - `executive_change_events`
  - `merger_acquisition_events`
  - `private_placement_relation_events`
  - `insider_transfer_completed`
  - `insider_transfer_declared_not_completed`
  - `treasury_stock_events`
  - `taiwan_index_futures_near_month`

- [ ] **Step 1: Add generic mdate family normalization tests**

Append to `tests/test_raw_family_normalization.py`:

```python
def test_generic_governance_family_uses_mdate_as_source_available_date():
    registry = _registry()
    registry["families"]["director_supervisor_holdings"] = {
        "availability_field": "mdate",
        "date_normalization": "date_only",
        "logical_key": ["ticker", "source_date"],
        "revision_field": "mdate",
        "selected_view": False,
    }
    result = normalize_raw_family(
        "director_supervisor_holdings",
        [{"coid": "2330", "mdate": "2025-01-15 00:00:00", "shares": 10, "source_row_id": "a"}],
        registry,
    )
    row = result["raw_rows"][0]
    assert row["ticker"] == "2330"
    assert row["source_date"] == "2025-01-15"
    assert row["source_available_date"] == "2025-01-15"
    assert row["shares"] == 10
```

- [ ] **Step 2: Add futures normalization test**

Append:

```python
def test_futures_near_month_uses_chinese_date_field():
    registry = _registry()
    registry["families"]["taiwan_index_futures_near_month"] = {
        "availability_field": "日期",
        "date_normalization": "date_only",
        "logical_key": ["date", "contract"],
        "revision_field": None,
        "selected_view": False,
    }
    result = normalize_raw_family(
        "taiwan_index_futures_near_month",
        [{"日期": "2025-01-02", "契約": "TXF202501", "收盤價": 23000, "source_row_id": "a"}],
        registry,
    )
    row = result["raw_rows"][0]
    assert row["date"] == "2025-01-02"
    assert row["source_available_date"] == "2025-01-02"
    assert row["contract"] == "TXF202501"
```

- [ ] **Step 3: Register normalizers**

In `raw_families.py`, set:

```python
_NORMALIZERS = {
    "trading_calendar": _normalize_trading_calendar,
    "monthly_sales": _normalize_monthly_sales,
    "financial_statement_raw": _normalize_financial_statement,
    "self_reported_numbers_raw": _normalize_self_reported_numbers,
    "taiwan_index_futures_near_month": _normalize_futures_near_month,
}
```

Do not register `AINVFQ1` or `APISHRACTW`.

- [ ] **Step 4: Run governance/futures tests**

Run:

```powershell
python -m pytest tests/test_raw_family_normalization.py -q
```

Expected: all tests pass.

**Quantitative Verification:**
- Every governance family reports `source_row_count`, `published_row_count`, `pit_null_count`, `pit_parse_failure_count`, `duplicate_logical_key_count`, and `unresolved_duplicate_count`.
- Futures reports `duplicate_date_contract_count`.

---

