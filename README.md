# Trading Management

Seven independent Python projects use synthetic fund, market, broker, and investor fixtures to reconcile daily fund books, compute multi-currency NAV snapshots, account for management and performance fees, manage investor subscriptions and redemptions, monitor prime broker collateral and financing, measure portfolio performance and investor reports, and produce illustrative regulatory drafts.

## Getting started

Start with the Fund Performance and Investor Reporting Dashboard. Its offline demo requires Python 3.9 or later and uses the standard library.

```bash
git clone https://github.com/vaibhavkapur/Trading-Management.git
cd Trading-Management/fund-performance-and-investor-reporting-dashboard
./scripts/setup.sh
./scripts/demo.sh
```

The demo prints gross and net returns, then publishes a revised report after a $125 management fee correction. Compare the two version IDs in the output and open the revised HTML report in [the sample exports](fund-performance-and-investor-reporting-dashboard/reports/demo_exports/). Each project has its own dependencies, demo, and validation commands. Run them from that project's directory.

## Projects

- [Integrated Hedge Fund Management Platform](integrated-hedge-fund-management-platform/) — Posts double-entry journals, reconciles positions and cash against broker and custodian records, and requires independent sign-off before publishing reports from a frozen snapshot.
- [Fund NAV Calculation Engine](fund-nav-calculation-engine/) — Computes exact multi-currency daily Net Asset Value, share-class allocations, and general-ledger reconciliations with SQLite trigger-enforced immutable snapshots and SHA-256 verified restatements.
- [Hedge Fund Fee Accounting Engine](hedge-fund-fee-accounting-engine/) — Calculates management and performance fees across share-class terms, high-water marks, hurdle conventions, and mid-period capital flows with an immutable SQLite double-entry ledger and versioned restatements.
- [Investor Subscription and Redemption Platform](investor-subscription-and-redemption-platform/) — Models investor onboarding, dealing-window cutoff controls, exact `Decimal` share issuance and redemption, cash reconciliation, maker-checker governance, and tamper-evident audit trails.
- [Prime Broker Collateral Management](prime-broker-collateral-management/) — Reconciles multi-broker collateral statements, detects cross-broker double-counting, calculates haircuts, concentration trims, and financing accruals, and gates collateral substitutions prior to settlement.
- [Fund Performance and Investor Reporting Dashboard](fund-performance-and-investor-reporting-dashboard/) — Measures time-weighted returns, risk, and strategy contributions. Reviewers can trace a fee restatement from the original investor report to its approved revision.
- [Regulatory Reporting Engine](regulatory-reporting-engine/) — Transforms governed fund snapshots into schema-validated illustrative XML and CSV regulatory drafts with full field-level evidence lineage, cross-report reconciliation, and cryptographically chained approval audits.

Each project README links to its architecture, data contracts, operator runbook, and validation evidence.
