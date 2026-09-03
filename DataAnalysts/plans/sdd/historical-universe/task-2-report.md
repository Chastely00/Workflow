STATUS: DONE

Files changed
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\security_panel.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_security_panel.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\historical-universe\task-2-report.md`

RED test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: FAIL during collection with `ImportError: cannot import name 'build_historical_security_panel' from 'data_analysts.security_panel'`.

GREEN test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: PASS, `3 passed in 0.03s`.

Self-review notes
- Kept existing `build_security_panel()` behavior unchanged and added an explicit regression test for its latest-only output shape.
- Added `build_historical_security_panel()` as a separate helper so Task 2 does not alter pipeline publish, universe builder, or verify behavior.
- `effective_date` is derived from the next trading day in sorted trading-calendar order.
- `adv20` is computed per ticker from current-and-past `traded_value` only, using a rolling window capped at 20 observations.
- Historical rows are emitted only when a price exists for `(as_of_date, ticker)`, preserving `source_max_date <= as_of_date` for this helper surface.

Concerns
- None for Task 2 scope.

---

Fix update: reviewer findings follow-up

STATUS: DONE

Scope
- Modified only `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\security_panel.py`
- Modified only `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_security_panel.py`
- Preserved existing `build_security_panel()` behavior
- Did not touch pipeline publish, universe builder, or verify gates

Root cause
- `effective_date` used one global next-trading-day map, so `TPEX` rows could inherit `TWSE` next dates.
- Duplicate `(date, ticker)` price rows were collapsed by `price_by_key` dict construction before diagnostics could see them.
- `effective_date_null_count` was incremented per `(as_of_date, ticker universe)` before checking whether a price-backed output row existed.

RED
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: `3 failed, 3 passed`
- Failing regressions:
  - market-specific next trading day expected `8088/TPEX -> 2025-01-07`, got `2025-01-06`
  - duplicate price rows expected first row retained and `duplicate_as_of_ticker_count == 1`, but later row overwrote earlier row
  - null effective-date diagnostics expected `1`, got `2`

Fix
- Added market-aware effective-date resolution:
  - use `trading_calendar.market`-specific next trading day when the security master has a market and that market has calendar coverage for the row date
  - fallback to global next-trading-day map only when the security master lacks a usable market/date-specific market calendar entry
- Replaced price dict overwrite path with `price_rows_by_key` lists so duplicates remain observable and diagnostics count every extra `(as_of_date, ticker)` row
- Emit panel rows from the first price row per `(as_of_date, ticker)` and count `effective_date_null_count` only for rows actually emitted

GREEN
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: `6 passed in 0.02s`

Concerns
- None for requested Task 2 fix scope.

---

Fix update: narrow calendar fallback correction

STATUS: DONE

Scope
- Modified only `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\security_panel.py`
- Modified only `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_security_panel.py`
- Preserved existing latest-only `build_security_panel()` behavior

Root cause
- `_effective_date_for_market()` fell back to the global next-trading-day map whenever a known market calendar did not contain the row `as_of_date`.
- That behavior violated the historical universe fail-closed contract: known `market` must use its own market calendar, and missing market coverage must surface as `effective_date = None`.

RED
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: FAIL, `test_historical_security_panel_known_market_does_not_fallback_to_global_calendar`
- Observed bug: TWSE row on `2025-01-03` incorrectly received global/TPEX next date `2025-01-06` instead of `None`

Fix
- Tightened `_effective_date_for_market()` so:
  - known non-blank `market` returns that market's next trading day only when the market calendar contains `as_of_date`
  - known `market` with missing calendar coverage or missing next trading day returns `None`
  - only unknown/blank `market` falls back to the global calendar
- Added regression coverage proving a TWSE security does not inherit a TPEX/global next date when TWSE lacks the `as_of_date`

GREEN
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: PASS, `7 passed in 0.02s`

Concerns
- None for requested narrow fix scope.
