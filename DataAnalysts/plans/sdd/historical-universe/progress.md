# Historical Universe SDD Progress

- Task 1: complete (review clean after output-contract effective_date fix; tests `11 passed`)
- Task 2: complete (review clean after market-calendar fallback fix; tests `7 passed`)
- Task 3: complete (review clean; tests `2 passed`; verify alignment deferred to Task 5)
- Task 4: complete (review clean after manifest provenance fix; tests `6 passed`)
- Task 5: complete (review clean; tests `19 passed`; historical verify gates added)
- Task 6: complete (review clean; inspect/docs summary added; tests `2 passed`)
- Task 7: complete
  - `python -m pytest -q` -> `95 passed`
  - `python -m data_analysts.cli run-backfill --root runs\real_all_products --families trading_calendar,daily_price_volume,security_master,daily_tradability --start-date 2026-01-01 --end-date 2026-01-31` -> `ready`
  - `python -m data_analysts.cli verify --root runs\real_all_products` -> `ready`
  - `inspect_artifacts(...)` compact summary -> `historical_universe.status == ready`, `historical_universe_file_count == 6`, `small_file_daily_partition_count == 0`
  - Compact historical universe check -> `files == 6`, `rows == 156388`, `bad_effective == 0`, `duplicate_membership_keys == 0`, `duplicate_rank_keys == 0`, `order_violations_asof_eff_rank == 0`, `small_file_daily_partition_count == 0`
  - Task 7 review clean; reviewer parquet check -> `duplicate_membership_keys == 0`, `duplicate_rank_keys == 0`, `order_violations_asof_eff_rank == 0`, `top100/300/500 max rows == 100/300/500`
  - Final review found fail-closed gaps; fixed multi-day top-N underfill diagnostics, required diagnostics gates, strict non-bool integer diagnostics schema, duplicate diagnostics blocking, `security_panel_history.effective_date` verification, and empty-universe stale cleanup
  - Historical universe smoke source families used: `trading_calendar`, `daily_price_volume`, `security_master`, `daily_tradability`
  - Regression fix: raw trading calendar rows now normalize via `date_rmk`/`zdate` in historical security panel path
  - Regression fix: stale `membership_by_date` paths are removed before historical `membership_by_year` publishing
