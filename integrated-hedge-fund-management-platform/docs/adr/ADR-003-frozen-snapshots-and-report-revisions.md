# ADR-003: Content-Hashed Frozen Snapshots and Versioned Report Revisions

- **Status**: Accepted
- **Date**: 2026-11-27
- **Context**: Official investor, risk, and administrator reports must remain reproducible and traceable; silent in-place mutation of historical numbers breaks auditability.
- **Decision**: Compute a canonical SHA-256 `content_hash` on every `PortfolioSnapshot`. Once frozen (`is_frozen=True`), the storage layer (`SQLiteStore.save_snapshot`) rejects overwrites (`SnapshotImmutableError`). `ReportingService.generate_report` rejects any unfrozen snapshot. Post-close restatements must freeze a new snapshot version (`v2`) and call `revise_report`, which requires a non-empty `reason`, computes a structured `ReportRevisionDiff`, requires dual-control approval, and marks the prior version `SUPERSEDED`.
- **Consequences**: Complete end-to-end provenance from published report -> frozen snapshot -> ledger entries -> fills/marks, with reviewable version diffs on every correction.
