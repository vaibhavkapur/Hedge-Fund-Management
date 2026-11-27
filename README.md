# Hedge Fund Management

Two independent Python projects model hedge fund operations with synthetic data. The management platform reconciles the fund’s books and freezes a snapshot at daily close. The reporting dashboard calculates returns from its own NAV and cash-flow fixtures and shows how an approved investor report changes after a fee correction.

## Getting started

Start with the Fund Performance and Investor Reporting Dashboard. Its offline demo requires Python 3.9 or later and uses the standard library.

```bash
git clone https://github.com/vaibhavkapur/Hedge-Fund-Management.git
cd Hedge-Fund-Management/fund-performance-and-investor-reporting-dashboard
./scripts/setup.sh
./scripts/demo.sh
```

The demo prints gross and net returns, then publishes a revised report after a $125 management fee correction. Compare the two version IDs in the output and open the revised HTML report in [the sample exports](fund-performance-and-investor-reporting-dashboard/reports/demo_exports/). Run each project’s setup and validation commands from its own directory.

## Projects

- [Integrated Hedge Fund Management Platform](integrated-hedge-fund-management-platform/) — Posts double-entry journals, reconciles positions and cash against broker and custodian records, and requires independent sign-off before publishing reports from a frozen snapshot.
- [Fund Performance and Investor Reporting Dashboard](fund-performance-and-investor-reporting-dashboard/) — Measures time-weighted returns, risk, and strategy contributions. Reviewers can trace a fee restatement from the original investor report to its approved revision.

Each project README links to its architecture, API reference, operator runbook, and validation evidence.
