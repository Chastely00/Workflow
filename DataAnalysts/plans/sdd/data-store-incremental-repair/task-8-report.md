# Task 8 — Fourth Review Remediation Report

Status: implementation complete; live `data_store` was not touched.

## Contract changes

- Superseded-path archival now persists an fsync-backed, atomic recovery intent to both the archive and jobs surfaces before moving payloads. If rollback cannot restore every item, recovery evidence retains the original manifest bytes, exact source/destination mapping, hash, size, error, and manual recovery step. Complete receipt creation has a durable fallback sidecar.
- Run verification now binds to the exact persisted run state, run id, family set, configuration hashes, pre-publication audit hash, and complete manifest identity set. The persisted scope is authoritative. A successful verify consumes the attestation by promoting both job states to `ready/complete` with `run_attestation.status=verified`; replay and stale/incomplete states fail closed.
- `partition_upsert`, `snapshot_by_value`, and `full_replace` all publish one immutable version and require one explicit normalized `active_version` matching every active artifact path.
- Family extraction destructively consumes each collection payload. The pipeline field-map seam also rewrites each source row in place and compacts filtered rows within the same list, so it does not retain complete source and mapped payloads simultaneously. A 1,000-row regression proves the same list and row identities survive while one-source-to-many-target aliases, lineage, and raw-key removal remain correct. This is not bounded-memory streaming: the Mongo cursor is still materialized one collection at a time, so peak memory is governed by the largest single collection rather than the whole store. Parquet inspection closes file handles on every branch.

## Verification evidence

- Focused archive/attestation/active-version/handle suite: `47 passed`.
- Focused publication/CLI regression suite: `82 passed`.
- Mapping seam and cross-family focused suite: `63 passed`.
- Full DataAnalysts suite: `409 passed in 26.36s`.
- No MongoDB extraction, production publication, archive, or live `data_store` mutation was performed.
