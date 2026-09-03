# TEJ Daily Market State Producer (bounded first)

## Objective

Build a reproducible, PIT-auditable `daily_market_state` producer from the
existing TEJ Mongo sources only.  The first production benchmark is bounded to
2024-01-01 through 2026-07-07; it must not scan or recompute full history.

## Non-negotiable semantics

- `TEJ.APISTOCK`/`security_master` defines lifecycle.  A security is absent
  after `delist_date` and the ETF execution engine liquidates it; no synthetic
  zero-volume rows are emitted after delisting.
- Prior to delisting, a simultaneous APIPRCD/APISTKATTR absence is a
  user-authorized `HALTED` with `ZERO_AUTHORIZED` amount.  Attribute suspension
  flags retain their more specific reason.
- No TWSE/TPEX network download or fallback is permitted.
- Raw Mongo is read-only.  Outputs are versioned candidate materialisations
  under `DataAnalysts/data_store`; no source collection is ever changed.
- Each materialised row records the four input-manifest hashes and an
  after-close availability contract so downstream ETF/Dollar-bar reads can
  reject unproven PIT alignment.

## Implementation slices

1. Add an extraction routine for APISTKATTR that lists collections once, uses
   narrow projections and bounded `mdate` predicates, and emits only one
   normalised row per `(date,ticker)`.  It batches work by ticker and does no
   per-session commits or SQLite staging.
2. Publish a candidate `daily_tradability` parquet/manifest with a complete
   partition inventory.  Reuse `trading_calendar`, `security_master`, and
   `daily_price_volume` manifests rather than rereading them in inner loops.
3. Build `daily_market_state` in a dense date-by-ticker pass: create lifecycle
   masks once, map price/attribute observations once, and classify the whole
   bounded range without per-row Mongo calls.
4. Add physical-schema and `DataGateway` consumer tests, including delisting,
   halt, missing source rows, and next-session execution timing.
5. Run a small correctness fixture first, then a real 2024-2026 materialisation
   and record wall-clock timings for extraction, classification, parquet write,
   and downstream 13-ETF bounded AFML smoke.  Only after bounded success is a
   2005-2026 run allowed.

## Performance acceptance

- The bounded run may make one bounded query per ticker collection but must not
  query Mongo once per date or reload the same parquet table inside a loop.
- Parquet is written in year-sized batches; manifest publication occurs once
  after all partitions are valid.
- Timing output is phase-separated, so later regressions can identify Mongo
  extraction, materialisation, or publication independently.
