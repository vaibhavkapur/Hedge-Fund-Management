# Data Contracts — Prime Broker Collateral Management

**Schema Version:** `1.0.0`

## 1. Units, Types, and Timestamp Semantics

- **Monetary Amounts, Prices, Quantities, Rates, Haircuts:** Encoded as **exact decimal strings** (e.g., `"1000000.00"`, `"0.025"`). IEEE-754 JSON floats are rejected with `VALIDATION_ERROR`.
- **Currencies:** Uppercase ISO-4217 3-letter codes. Supported minor units: `USD` (2), `EUR` (2), `GBP` (2), `CHF` (2), `JPY` (0). Unknown currencies raise `UNSUPPORTED_INPUT`.
- **Business Dates:** ISO-8601 calendar dates `YYYY-MM-DD`.
- **Timestamps (`received_at`):** ISO-8601 UTC timestamps with explicit timezone (`Z` or `+00:00`). Naive timestamps raise `VALIDATION_ERROR`.
- **Sign Conventions:**
  - `DEBIT_FINANCING` cash balances must be `<= 0` (e.g., `"-600000.00"`).
  - `FREE_CASH`, `MARGIN_CASH`, `SHORT_PROCEEDS` must be `>= 0`.
  - Financing accruals: positive = income to fund (credit interest / positive short rebate); negative = financing expense payable to prime broker.
  - Margin `raw_surplus_or_deficit_base = posted_eligible_collateral_base - margin_requirement_base` (negative indicates shortfall).

## 2. Canonical Inputs

### 2.1 Collateral Policy (`config/default_policy.json`)

- Required top-level fields: `schema_version`, `policies[]`, `rounding_mode` (`ROUND_HALF_EVEN` | `ROUND_HALF_UP` | `ROUND_DOWN` | `ROUND_UP`).
- Each policy defines: `policy_id`, `version` (int >= 1), `broker_id`, `base_currency`, `effective_date`, `default_day_count`, `minimum_transfer_amount`, `rounding_increment` (> 0), `reconciliation_tolerance` (>= 0), and `rules[]`.
- Each rule defines: `asset_class`, `rating_tier` (`ANY` or specific rating), `maturity_bucket` (`ANY` or bucket), `eligible` (bool), `haircut_pct` (`[0, 1]`), `concentration_limit_pct` (`null` or `[0, 1]`), `currency_mismatch_addon_pct` (`[0, 1]`).

### 2.2 Financing Rate Schedules (`config/financing_rates.json`)

- Required fields per schedule: `schedule_id`, `broker_id`, `currency`, `benchmark_name`, `benchmark_rate`, `debit_spread_bps` (>= 0), `credit_spread_bps` (>= 0), `short_rebate_spread_bps` (>= 0), `day_count` (`ACT_360` | `ACT_365F` | `ACT_ACT_ISDA` | `THIRTY_360`), `effective_date`.

### 2.3 Broker Statement (`BrokerStatement`)

- Required fields: `schema_version`, `statement_id`, `version` (int >= 1), `broker_id`, `account_id`, `business_date`, `received_at`, `base_currency`, `positions[]`, `cash_balances[]`, `margin_requirement`.
- Optional fields: `supersedes_statement_id`, `reported_collateral_value`, `reported_financing_accrual`.
- Normalization aliases accepted on input:
  - Asset classes: `UST`/`TREASURY` -> `US_TREASURY`; `SOV`/`SOVEREIGN` -> `SOVEREIGN_BOND`; `CORP_IG`/`IG_CORP` -> `IG_CORPORATE_BOND`; `CORP_HY`/`HY_CORP` -> `HY_CORPORATE_BOND`; `EQ_LC` -> `EQUITY_LARGE_CAP`; `EQ_SC` -> `EQUITY_SMALL_CAP`; `MMF` -> `MONEY_MARKET_FUND`.
  - Encumbrance: `FREE`/`AVAILABLE` -> `UNENCUMBERED`; `PLEDGED`/`MARGIN_PLEDGED` -> `PLEDGED_MARGIN`; `REHYP` -> `REHYPOTHECATED`; `PENDING_OUT` -> `PENDING_TRANSFER_OUT`; `PENDING_IN` -> `PENDING_TRANSFER_IN`; `FAILED` -> `FAILED_TRANSFER`.
  - Cash types: `EXCESS_CASH` -> `FREE_CASH`; `COLLATERAL_CASH` -> `MARGIN_CASH`; `MARGIN_DEBIT` -> `DEBIT_FINANCING`; `SHORT_CREDIT` -> `SHORT_PROCEEDS`.

### 2.4 Internal Ledger Snapshot & Transfers (`InternalLedgerSnapshot`)

- Required fields: `schema_version`, `snapshot_id`, `version`, `broker_id`, `account_id`, `business_date`, `positions[]`, `cash_balances[]`, `transfers[]`.
- Each transfer requires: `transfer_id`, `from_broker_id`, `to_broker_id`, `lot_id`, `security_id`, `quantity`, `currency`, `market_value`, `status` (`SETTLED` | `PENDING` | `FAILED` | `CANCELLED`), `trade_date`, `settlement_date` (`>= trade_date`), optional `failure_reason`.

### 2.5 Substitution Request (`SubstitutionRequest`)

- Required fields: `schema_version`, `request_id`, `broker_id`, `account_id`, `business_date`, `requested_by`, `release_lot_ids[]`, `pledge_positions[]`.

## 3. Canonical Outputs & Stable Identifiers

- **`run_id`:** `RUN-<broker_id>-<business_date>-V<version>-<10-char SHA-256 prefix>` computed from statement hash, snapshot version, policy version, and accrual dates.
- **`call_id`:** `MC-<broker_id>-<business_date>-V<version>-<10-char SHA-256 prefix>`.
- **`break_id`:** `BRK-<broker_id>-<business_date>-<10-char SHA-256 prefix>`.
- **`review_id`:** `SUBREV-<broker_id>-<10-char SHA-256 prefix>`.
- **Report Envelope:** `{ schema_version, scenario_name, metadata, counters, reconciliations[], substitutions[] }`.

## 4. Validation & Error Codes

| Error Code | Exception Class | Trigger Condition |
|---|---|---|
| `VALIDATION_ERROR` | `ValidationError` | Raw float value, missing required field, invalid date/timestamp, negative quantity, duplicate lot inside statement, invalid cash sign |
| `UNSUPPORTED_INPUT` | `UnsupportedInputError` | Unsupported schema version, currency, asset class, encumbrance status, cash type, rounding mode, or day-count convention |
| `MISSING_CONFIGURATION` | `MissingConfigurationError` | Missing broker collateral policy, missing FX rate pair, or missing financing rate schedule |
| `DUPLICATE_STATEMENT` | `DuplicateStatementError` | Same `statement_id` with different content hash, or non-increasing `version` for `(broker_id, account_id, business_date)` |
| `COLLATERAL_DOUBLE_COUNTING` | `DoubleCountingError` | Same `lot_id` actively counted at multiple brokers or pledged twice |
| `DAY_COUNT_BOUNDARY_ERROR` | `DayCountBoundaryError` | `end_date < start_date`, disallowed zero-day period, or invalid day-count boundary |
| `PERSISTENCE_ERROR` | `PersistenceError` | Missing optional PostgreSQL driver or journal storage constraint violation |

## 5. Fixture Inventory

- **Valid Reference Oracle:** `data/fixtures/reference_oracle.json` (hand-calculated clean baseline).
- **Multi-Broker Integration & Substitution Bundle:** `data/fixtures/demo_multi_broker_bundle.json`.
- **Invalid & Unsupported Fixtures:** `data/fixtures/invalid_payloads.json`.
