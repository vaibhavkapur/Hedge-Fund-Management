# Integration Boundaries — `Trading-Management`

## 1. Sibling Repository Verification

The `Trading-Management` repository hosts independent sibling projects at the repository root:

- `integrated-hedge-fund-management-platform/`
- `fund-nav-calculation-engine/`
- `investor-subscription-and-redemption-platform/`
- `prime-broker-collateral-management/`
- `fund-performance-and-investor-reporting-dashboard/`
- `regulatory-reporting-engine/`

To keep each project independently runnable and decoupled, `hedge-fund-fee-accounting-engine` does **not** import sibling code directly. Instead, it exposes independent, runnable adapter interfaces in `src/hedge_fund_fee_engine/adapters.py` so neighboring projects can connect over versioned JSON/dict contracts.

## 2. Adapter Boundaries

| Neighbor Project | Direction | Adapter Class | Contract Summary |
|---|---|---|---|
| `fund-nav-calculation-engine` | Upstream -> Fee Engine | `NavSnapshotFeedAdapter` | Translates pre-fee NAV outputs (`valuation_id`, `pre_fee_nav_per_share`, `share_class`, `currency`, `period_start_date`, `valuation_date`) into canonical `NavSnapshot`. |
| `investor-subscription-and-redemption-platform` | Upstream -> Fee Engine | `CapitalFlowFeedAdapter` | Translates investor subscription/redemption orders (`order_id`, `trade_date`, `dealing_nav_per_unit`, `amount`/`units`) into canonical `CapitalFlow`. |
| `regulatory-reporting-engine` | Fee Engine -> Downstream | `RegulatoryLedgerExportAdapter` | Exports versioned, reconciled per-lot fee schedules (`export_schema_version: "1.0.0"`) suitable for Form PF / Annex IV style fee summary aggregation. |

## 3. Compatibility and Isolation Rules

- **Zero Shared Mutable State**: Adapters exchange immutable JSON-serializable payloads; the fee engine owns its own SQLite ledger.
- **No Production / External Credentials**: No live fund administrator, prime broker, custodian, or proprietary/employer integrations exist or are enabled.
- **Offline Standalone Execution**: Every adapter and CLI command runs 100% offline with zero network dependencies.
