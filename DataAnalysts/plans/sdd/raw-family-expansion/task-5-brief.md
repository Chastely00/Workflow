## Task 5: Financial and Self-Reported Selected PIT Views

**Files:**
- Modify: `src/data_analysts/raw_families.py`
- Modify: `src/data_analysts/pipeline.py`
- Test: `tests/test_raw_family_normalization.py`
- Test: `tests/test_raw_family_pipeline.py`

**Boundary:**
- This task hardens selected PIT surfaces only.
- It must not filter raw canonical rows to `no = Q`; raw output preserves `A`, `Q`, and `TTM`.
- It must not use selected PIT output to tune strategy or universe behavior.

**Interfaces:**
- Produces selected view rows with columns:

```text
decision_date
ticker
no or key3
sem
curr
merg
period_end_date
source_available_date
revision_date
source_dataset_id
source_collection
source_row_id
data_cutoff_at
```

- [ ] **Step 1: Add tests for A/Q/TTM preservation and selected Q convenience counts**

Append to `tests/test_raw_family_normalization.py`:

```python
def test_financial_statement_raw_preserves_a_q_ttm_and_reports_selected_q_count():
    result = normalize_raw_family(
        "financial_statement_raw",
        [
            {"coid": "2330", "no": "A", "sem": "4", "curr": "TWD", "merg": "Y", "endd": "2024-12-31", "key3": "2025-03-31", "mdate": "2025-04-01", "eps": 40, "source_row_id": "a"},
            {"coid": "2330", "no": "Q", "sem": "1", "curr": "TWD", "merg": "Y", "endd": "2025-03-31", "key3": "2025-05-15", "mdate": "2025-05-16", "eps": 10, "source_row_id": "q"},
            {"coid": "2330", "no": "TTM", "sem": "1", "curr": "TWD", "merg": "Y", "endd": "2025-03-31", "key3": "2025-05-15", "mdate": "2025-05-16", "eps": 42, "source_row_id": "t"},
        ],
        _registry(),
        decision_dates=["2025-05-31"],
    )
    assert {row["no"] for row in result["raw_rows"]} == {"A", "Q", "TTM"}
    assert result["diagnostics"]["rows_by_no"] == {"A": 1, "Q": 1, "TTM": 1}
    assert result["diagnostics"]["selected_no_q_row_count"] == 1
```

- [ ] **Step 2: Add unresolved duplicate selected PIT test**

Append:

```python
def test_financial_statement_selected_pit_unresolved_duplicate_fails_closed():
    with pytest.raises(RawFamilyError, match="unresolved duplicate"):
        normalize_raw_family(
            "financial_statement_raw",
            [
                {"coid": "2330", "no": "Q", "sem": "2", "curr": "TWD", "merg": "Y", "endd": "2025-06-30", "key3": "2025-08-14", "mdate": "2025-08-20", "eps": 10, "source_row_id": "a"},
                {"coid": "2330", "no": "Q", "sem": "2", "curr": "TWD", "merg": "Y", "endd": "2025-06-30", "key3": "2025-08-14", "mdate": "2025-08-20", "eps": 11, "source_row_id": "b"},
            ],
            _registry(),
            decision_dates=["2025-08-31"],
        )
```

- [ ] **Step 3: Implement selected diagnostics**

In `_selected_rows()`, accumulate:

```python
{
    "decision_date_count": len(decision_dates or []),
    "eligible_row_count": sum(selected_diag["eligible_row_count"] for each decision_date),
    "future_row_count": sum(selected_diag["future_row_count"] for each decision_date),
    "selected_row_count": total_selected_rows,
    "selected_no_q_row_count": sum(1 for row in selected_rows if row.get("no") == "Q"),
    "selected_key3_category_counts": dict(Counter(row["key3"] for row in selected_rows if "key3" in row)),
}
```

On `PitError`, raise:

```python
raise RawFamilyError(str(exc)) from exc
```

- [ ] **Step 4: Run selected PIT tests**

Run:

```powershell
python -m pytest tests/test_raw_family_normalization.py tests/test_pit_selection.py -q
```

Expected: all tests pass.

**Quantitative Verification:**
- raw rows by `no` include all observed `A`, `Q`, and `TTM`.
- `selected_no_q_row_count` is reported.
- unresolved duplicate selected PIT rows raise `RawFamilyError`.

---

