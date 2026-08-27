# ETF Tricks AFML Source-Capability Audit

Audit date: 2026-08-27

Scope: read-only inspection of the current `DataAnalysts/data_store` manifests, governed artifact contract, bounded IX0001 rows, and the validated ETF Trick result at `.artifacts/etf_tricks/performance/optimized-final-20240101-20260707`.

This is Phase 0 source evidence. It does not claim that the AFML dataset implementation exists or that timestamp-level historical PIT has been proven.

## Dataset and Grain Summary

| Input | Intended grain | Current evidence | Result |
|---|---|---|---|
| ETF Trick Daily NAV/amount | `(date, etf_id)` | 7,878 rows = 606 dates x 13 ETFs; no duplicate key, null/nonpositive NAV, null/negative amount, or missing constituent amount | Usable bounded input |
| IX0001 market series | `(date, ticker)` filtered to `IX0001` | 606 rows from 2024-01-02 through 2026-07-07; exactly matches 606 TWSE trading days | Available |
| Trading calendar | `(date, market)` | 606 bounded TWSE sessions; `source_available_date` populated for all | Available |
| VPIN inputs | Tick/trade plus volume bucket and buy/sell classification | No tick grain, aggressor side, buy volume, or sell volume field | Unavailable |
| Kyle lambda inputs | Signed order flow or verified aggressor side | No signed-flow or aggressor-side field | Unavailable |
| Synthetic ETF ATR/ADX inputs | Synchronized true open/high/low/close for each synthetic ETF Trick | Constituent daily OHLC exists, but no true synthetic ETF Trick OHLC | Unavailable |
| Taiwan VIX | PIT-safe implied-volatility series | No manifest whose name identifies VIX or volatility | Unavailable |

## Source Capability Matrix

| Feature/source | Status | Evidence | PIT/coverage assessment | Required handling |
|---|---|---|---|---|
| IX0001 `close`, `amt` | `PARTIAL_COVERAGE` | `daily_price_volume` has `date,ticker,close,traded_value`; IX0001 has 606/606 expected dates, zero duplicate dates, zero null/nonpositive close or amount | Content and date-only after-close policy are verified, but row revisions/publication timestamps are not | Preserve date-only availability assumption, compute a selected-row content hash, and mark `PIT_REVISION_UNVERIFIED` |
| Trading calendar | `AVAILABLE_VERIFIED` for bounded content | `trading_calendar` has `date,market,is_trading_day,source_available_date`; 606/606 bounded rows populated | `source_available_date == date` for all bounded rows | Use it to derive the next eligible session; do not infer by calendar-day addition |
| VPIN | `UNAVAILABLE_SOURCE_GRAIN` | Current market data is daily; no equal-volume bucket inputs or buy/sell classification | Cannot reconstruct true VPIN from daily totals | Do not create a `vpin` feature; a future proxy must be separately named |
| Kyle lambda | `UNAVAILABLE_SOURCE_GRAIN` | No signed order flow, aggressor side, or equivalent verified field | Daily unsigned value cannot identify price impact coefficient in the required sense | Do not create `kyle_lambda` |
| ATR | `UNAVAILABLE_SOURCE_GRAIN` | No synchronized true OHLC for the synthetic ETF Tricks | Weighted constituent OHLC or close-path extrema are not a traded instrument OHLC | Keep formal ATR unavailable |
| ADX | `UNAVAILABLE_SOURCE_GRAIN` | Same missing synthetic OHLC contract as ATR | Close-path directional movement is not formal ADX | Keep formal ADX unavailable |
| Taiwan VIX | `UNAVAILABLE_SOURCE_GRAIN` | No VIX/volatility manifest exists | `taiwan_index_futures_near_month` is futures data, not implied volatility | Use IX0001 rolling volatility only under its own name |

## Identity and PIT Findings

### Confirmed usable

- Current `daily_price_volume` canonical-JSON manifest hash: `f8b845c2935b86c4d5cd79fc6d12794a38e4777bb89d364955eed5f407f7a734`.
- Current `trading_calendar` canonical-JSON manifest hash: `558b9e6430a4a37edaf6ebd850e6a50e2ed00c889ae4d0ed41098cda8d839955`.
- `DataAnalysts/configs/artifact_contracts.json` file hash: `68a751336e5ec3cf0e34118804b774a72296a0f9d0c75efe2f97a295bce30387`.
- The bounded ETF result metadata points to the same current daily-price and calendar manifest hashes.
- The governed artifact contract defines the daily-price key as `(date,ticker)`, availability field as `date`, and PIT policy as `source_date_lagged_to_decision_date`.
- Bounded ETF input contains all 13 governed ETF IDs with no duplicate `(date,etf_id)` key.

### High-severity PIT limitations

1. `daily_etf` has no `source_available_at` field. Observation date alone cannot support trading-facing knowledge-time filtering.
2. Both bounded IX0001 and calendar rows have only the sentinel `data_cutoff_at = 1970-01-01T00:00:00Z`. That field is not usable as a historical ingestion or publication timestamp.
3. The current manifests do not cryptographically bind each Parquet partition with a content hash. A manifest hash proves the metadata snapshot, not unchanged physical bytes.
4. No row-vintage/publication-history artifact proves how later revisions affected historical daily market rows.
5. The physical daily-price Parquet `date` field is stored as ISO string, although consumers normalize it to timestamps after reading. Predicate filters that bind timestamp scalars directly to the physical field fail; the filtered reader must inspect physical schema, bind a same-type filter, then normalize and revalidate dates.

Impact: the bounded data is structurally suitable for after-close causal research, but strict revision-safe historical PIT is not yet proven. Trading-facing outputs must remain `PIT_REVISION_UNVERIFIED` until the AFML adapter derives explicit availability and stores selected-row content hashes; it must never use file mtime or the current batch timestamp as historical availability.

## Checks Performed

- Manifest status, columns, row count, date range, PIT policy, and canonical JSON hash.
- Governed artifact-contract key, partition, availability field, and PIT policy.
- Predicate-pushed scan of only 2024-2026 IX0001 partitions/rows; the 9.7M-row universe was not materialized.
- Physical-schema check and a failed timestamp-vs-string predicate reproduction before the corrected same-type bounded scan.
- IX0001 key uniqueness, expected-session coverage, date validity, close validity, and amount validity.
- Bounded ETF result ID coverage, key uniqueness, NAV validity, amount validity, quality counters, and source-manifest identity match.
- Manifest-name and schema search for VIX, tick-side, aggressor-side, signed-flow, and buy/sell-volume capability.

## Required Automated Gates

1. Reject any IX0001 bounded scan with missing/duplicate expected TWSE dates, null/nonpositive close, or null/nonpositive amount.
2. Hash the exact sorted IX0001 and trading-calendar rows consumed by a run in addition to hashing manifests/config.
3. Derive `source_available_at` from the governed PIT policy; for date-only daily prices, record `AFTER_CLOSE_DATE_ONLY` and prohibit same-close execution.
4. Require every Dollar bar to satisfy `bar_available_at >= max(member source availability)` and every feature to satisfy `feature_available_at >= bar_available_at`.
5. Ensure `for_trading` returns no label columns and no row whose feature, source, or calibration version was unavailable at `decision_time`.
6. Keep VPIN, Kyle, ATR, ADX, and VIX absent from the formal feature schema until a new capability audit proves the required grain.

## Current Availability

- Available now: validated 13-ETF Daily NAV/`etf_amount`, complete bounded IX0001 close/amount, TWSE trading calendar, manifest/config identity, and an honest daily after-close PIT policy.
- Missing/limited now: row-level publication timestamps, revision vintages, physical partition hashes, synthetic ETF true OHLC, tick aggressor data, signed order flow, and Taiwan VIX.
