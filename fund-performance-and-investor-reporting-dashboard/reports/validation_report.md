# Validation & Test Evidence Report

- **Generated (UTC):** `2026-11-27T09:53:58Z`
- **Python / Platform:** `3.9.6` / `macOS-26.5.1-arm64-arm-64bit`
- **Data Classification:** `SIMULATED / SYNTHETIC FIXTURES ONLY`
- **Result:** `PASS` (25/25 passed in 151.01 ms)
- **Baseline Benchmark:** Mean full 90-day multi-currency analytics latency = `6.54 ms` (n=10 runs)

## Hand-Calculated Verification

| Metric | Expected | Computed | Match |
|---|---:|---:|---|
| `cumulative_gross_twr_usd` | `0.14345` | `0.14345` | `PASS` |
| `cumulative_net_twr_eur` | `0.24042545` | `0.24042545` | `PASS` |
| `cumulative_net_twr_usd` | `0.1276595` | `0.1276595` | `PASS` |
| `max_net_drawdown_peak_date` | `2026-02-02` | `2026-02-02` | `PASS` |
| `max_net_drawdown_recovery_date` | `2026-02-05` | `2026-02-05` | `PASS` |
| `max_net_drawdown_trough_date` | `2026-02-03` | `2026-02-03` | `PASS` |
| `max_net_drawdown_usd` | `-0.1` | `-0.1` | `PASS` |

## Test Cases Executed

| Test ID | Status |
|---|---|
| `tests.integration.test_storage_and_api.TestStorageAdaptersAndHttpApi.test_end_to_end_http_workflow_and_exports` | `PASS` |
| `tests.integration.test_storage_and_api.TestStorageAdaptersAndHttpApi.test_point_in_time_as_of_snapshot_retrieval` | `PASS` |
| `tests.integration.test_storage_and_api.TestStorageAdaptersAndHttpApi.test_postgres_adapter_fails_safely_without_driver_and_schema_exists` | `PASS` |
| `tests.scenarios.test_required_scenarios.TestRequiredPlanScenarios.test_scenario_01_cash_subscription_redemption` | `PASS` |
| `tests.scenarios.test_required_scenarios.TestRequiredPlanScenarios.test_scenario_02_missing_nav` | `PASS` |
| `tests.scenarios.test_required_scenarios.TestRequiredPlanScenarios.test_scenario_03_zero_volatility` | `PASS` |
| `tests.scenarios.test_required_scenarios.TestRequiredPlanScenarios.test_scenario_04_currency_conversion` | `PASS` |
| `tests.scenarios.test_required_scenarios.TestRequiredPlanScenarios.test_scenario_05_report_revision` | `PASS` |
| `tests.scenarios.test_required_scenarios.TestRequiredPlanScenarios.test_scenario_06_paper_live_mixing` | `PASS` |
| `tests.scenarios.test_required_scenarios.TestRequiredPlanScenarios.test_scenario_07_unauthorized_access` | `PASS` |
| `tests.unit.test_domain_and_validation.TestDomainAndValidation.test_environment_homogeneity_rejects_mixed` | `PASS` |
| `tests.unit.test_domain_and_validation.TestDomainAndValidation.test_rejects_availability_before_as_of` | `PASS` |
| `tests.unit.test_domain_and_validation.TestDomainAndValidation.test_rejects_broken_ledger_bridge` | `PASS` |
| `tests.unit.test_domain_and_validation.TestDomainAndValidation.test_rejects_net_exposure_exceeding_gross` | `PASS` |
| `tests.unit.test_domain_and_validation.TestDomainAndValidation.test_rejects_non_positive_nav_and_nan` | `PASS` |
| `tests.unit.test_domain_and_validation.TestDomainAndValidation.test_rejects_non_utc_timestamp` | `PASS` |
| `tests.unit.test_domain_and_validation.TestDomainAndValidation.test_rejects_roll_forward_break` | `PASS` |
| `tests.unit.test_domain_and_validation.TestDomainAndValidation.test_valid_snapshot_passes` | `PASS` |
| `tests.unit.test_metrics_hand_calculated.TestHandCalculatedMetrics.test_carino_attribution_sums_identically_to_fund_gross_twr` | `PASS` |
| `tests.unit.test_metrics_hand_calculated.TestHandCalculatedMetrics.test_end_of_day_cash_flow_timing` | `PASS` |
| `tests.unit.test_metrics_hand_calculated.TestHandCalculatedMetrics.test_hand_calculated_fixture_exact_match` | `PASS` |
| `tests.unit.test_risk_and_benchmark.TestRiskAndBenchmark.test_benchmark_gap_is_flagged` | `PASS` |
| `tests.unit.test_risk_and_benchmark.TestRiskAndBenchmark.test_benchmark_identical_series_zero_tracking_error` | `PASS` |
| `tests.unit.test_risk_and_benchmark.TestRiskAndBenchmark.test_var_cvar_ordering` | `PASS` |
| `tests.unit.test_risk_and_benchmark.TestRiskAndBenchmark.test_zero_volatility_returns_none_and_flags` | `PASS` |
