# SAAT Multi-Strategy Research Fund (Synthetic) — Performance & Risk Report

> **SIMULATED / SYNTHETIC DATA — NOT LIVE OR AUDITED TRACK RECORD. Results are generated for methodological demonstration and internal verification only.**

| Metadata | Value |
|---|---|
| Immutable Version ID | `RPT-2026Q1-ALPHA-v2-9e7f9f9b` |
| Content SHA-256 | `2507b9e83f2c78db2250f80a1f5c7e1c057303e95d8bc0d2de629ffd6f101d18` |
| Series / Revision | `RPT-2026Q1-ALPHA` (rev 2) |
| Supersedes | `RPT-2026Q1-ALPHA-v1-ec119501` |
| Revision Reason | Post-close management fee accrual restatement (+$125.00) |
| Status / Audience | `PUBLISHED` / `INVESTOR` |
| Environment / Data Status | `SIMULATED` / `SYNTHETIC` |
| Period | `2026-01-02` to `2026-05-07` |
| As-Of UTC | `2026-05-07T21:00:00Z` |
| Reporting Currency | `USD` (Fund base: `USD`) |
| Methodology | `SAAT-PERF-METH-1.0.0` |
| Approval Chain | Author: `analyst_maya` / Reviewer: `reviewer_alex` / Approver: `approver_cto` |

## Performance Summary (Gross vs. Net)

| Metric | Gross | Net |
|---|---:|---:|
| Cumulative TWR | -9.93% | -10.68% |
| Modified Dietz | -10.27% | -11.04% |
| Annualized Return (CAGR) | -25.39% | -27.12% |
| Annualized Volatility | 7.75% | 7.70% |
| Sharpe Ratio | -4.25 | -4.59 |
| Sortino Ratio | -5.07 | -5.37 |
| Max Drawdown | -11.27% | -11.76% |
| Calmar Ratio | -2.25 | -2.31 |
| Historical Daily VaR (95%) | -0.91% | -0.92% |
| Historical Daily CVaR (95%) | -0.96% | -0.97% |

## Ledger Reconciliation Bridge

- Opening NAV: `25,000,000.00 USD`
- Subscriptions / Redemptions: `+8,000,000.00` / `-2,500,000.00` (Net CF: `5,500,000.00`)
- Gross P&L / Total Fees / Net P&L: `-2,932,956.14` / `221,325.90` / `-3,154,282.04`
- Closing NAV: `27,345,717.96 USD` (Bridge Residual: `0.0`)
- All Snapshots Reconciled: `True` (`90` snapshots)

## Benchmark Comparison (`BM-SYNTH-6040-USD`)

- Benchmark Cumulative Return: -4.74%
- Active Return (Net vs. Benchmark): -5.94%
- Tracking Error (Ann.): 10.41%
- Information Ratio: -1.77
- Beta / Ann. Alpha: 0.32 / -29.86%

## Strategy Attribution (Carino-Linked)

| Strategy | Asset Class | Avg Weight | Gross P&L | Linked Contrib | TCA Costs |
|---|---|---:|---:|---:|---:|
| G10 FX Relative Value | FX | 14.00% | 43,919.00 | 0.20% | 20,099.44 |
| Cross-Asset Volatility Carry | Options | 18.00% | -376,910.54 | -1.27% | 27,599.68 |
| Equity StatArb | Equities | 38.00% | -934,615.74 | -3.12% | 55,998.95 |
| Systematic Macro Trend | Futures | 30.00% | -1,665,348.86 | -5.74% | 44,653.05 |

## Disclosures & Metric Quality Flags

- **Fee Basis**: Net metrics reflect accrued fees: 150.0 bps/yr management fee, 20.0% performance fee (with high-water mark, hurdle 4.00%/yr). Gross metrics exclude management/performance fees.
- **Currency Basis**: Reported in fund base currency USD; no FX translation applied.
- **Audit Status**: Data status: SYNTHETIC. Ledger reconciliation: ALL SNAPSHOTS RECONCILED.

- `[WARNING] SIMULATED_DATA` (fund.environment): Underlying fund series is SIMULATED / SYNTHETIC — not live performance.
- `[WARNING] UNAUDITED_DATA` (fund.data_status): Data status is SYNTHETIC; not an audited investor statement.
