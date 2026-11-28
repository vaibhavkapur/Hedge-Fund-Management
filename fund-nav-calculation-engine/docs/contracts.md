# Data Contracts & Error Catalog

## 1. Conventions, Units & Semantics

- **Numeric Encoding:** All monetary amounts, prices, FX rates, fees, and share quantities MUST be represented as base-10 decimal strings (e.g., `"102.50"`) or integers in JSON. Binary JSON floats (e.g., `102.5`) are rejected with `ValidationError` (`NAV-1002`) to prevent precision drift.
- **Currencies:** 3-letter uppercase ISO-4217 codes (`^[A-Z]{3}$`). Every currency appearing in inputs must exist in `ValuationConfig.currency_precisions`.
  - Default minor-unit precisions: `USD: 2`, `EUR: 2`, `GBP: 2`, `CHF: 2`, `JPY: 0`, `KWD: 3`.
- **Rounding:** Explicit `rounding_mode` in `ValuationConfig` (default: `ROUND_HALF_EVEN`). Supported modes: `ROUND_HALF_EVEN`, `ROUND_HALF_UP`, `ROUND_DOWN`, `ROUND_UP`.
- **Dates & Timestamps:**
  - Business/valuation dates: ISO-8601 `YYYY-MM-DD`.
  - Timestamps (`as_of_timestamp`, `submitted_at`, `calculated_at`): ISO-8601 with explicit timezone offset or `Z`, normalized internally to UTC. Naive timestamps are rejected.
  - Dealing cutoff: `flow_cutoff_utc` (`HH:MM:SS` in UTC) applied on `valuation_date`. Flows with `submitted_at <= valuation_date T flow_cutoff_utc` are eligible; flows strictly after cutoff follow `timing_rule` (`pre_nav` raises `NAV-3003`) and `post_cutoff_policy` (`defer` or `reject`).
- **Deterministic Identifiers:**
  - `input_hash`: SHA-256 of canonically sorted `{"bundle": ..., "config": ...}`.
  - `snapshot_id`: `SNAP-{fund_id}-{valuation_date}-v{version}-{input_hash[:12]}`.
  - `output_hash`: SHA-256 of deterministic `NavCalculationResult.core_payload_dict()`.

## 2. Canonical Input Contract (`ValuationInputBundle`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `bundle_id` | string | Yes | Unique batch identifier |
| `fund_id` | string | Yes | Must match `ValuationConfig.fund_id` |
| `valuation_date` | ISO date | Yes | Business valuation date |
| `input_version` | string | Yes | Semantic or monotonic source version tag |
| `positions` | `PositionRecord[]` | No (default `[]`) | `position_id`, `instrument_id`, `instrument_type` (`equity`, `bond`, `cash`, `etf`, `fund_unit`, `derivative_mtm`), `quantity`, `cost_basis`, `currency` |
| `prices` | `PriceRecord[]` | No | `price_id`, `instrument_id`, `price`, `currency`, `price_date`, `as_of_timestamp`, `pricing_source`, `is_override`, `override_reason` |
| `fx_rates` | `FxRateRecord[]` | No | `fx_id`, `base_currency`, `quote_currency`, `rate` (> 0, 1 unit of `base_currency` in `quote_currency`), `rate_date`, `as_of_timestamp` |
| `liabilities` | `LiabilityRecord[]` | No | `liability_id`, `category`, `amount` (>= 0), `currency`, optional `share_class_id` |
| `accruals` | `AccrualRecord[]` | No | `accrual_id`, `accrual_type`, `direction` (`asset` or `liability`), `amount` (>= 0), `currency`, `period_start`, `period_end`, optional `share_class_id` |
| `share_classes` | `ShareClassRecord[]` | Yes (>= 1) | `share_class_id`, `name`, `currency`, `opening_shares` (>= 0), `management_fee_bps`, `launch_nav_per_share` (> 0), optional `prior_nav_per_share`, optional `prior_net_assets_base` |
| `capital_flows` | `CapitalFlowRecord[]` | No | `flow_id`, `share_class_id`, `flow_type` (`subscription`/`redemption`), `currency`, `trade_date`, `settlement_date`, `submitted_at`, `amount` or `shares`, `status` (`confirmed`/`pending`/`cancelled`), `timing_rule` (`pre_nav`/`post_nav`) |
| `ledger_balances` | `LedgerBalanceRecord[]` | No | `ledger_account_id`, `account_type` (`cash`, `securities_cost`, `receivables`, `payables`, `accrued_expenses`, `capital_subscriptions`, `capital_redemptions`), `balance`, `currency`, `as_of_date` |

## 3. Canonical Output Contract (`NavCalculationResult`)

Key output fields serialized deterministically via `result.to_dict()`:

- **Metadata & Lineage:** `snapshot_id`, `fund_id`, `valuation_date`, `version`, `status` (`draft`/`approved`/`frozen`), `prior_snapshot_id`, `restatement_reason`, `config_version`, `input_hash`, `output_hash`, `calculated_at`.
- **Fund Totals (in `base_currency`):**
  - Pre-flow: `pre_flow_gross_assets`, `pre_flow_liabilities`, `pre_flow_net_assets`
  - Capital flows: `total_subscriptions`, `total_redemptions`, `net_capital_flows`
  - Closing: `gross_assets`, `total_liabilities`, `net_assets` (invariant: `net_assets == gross_assets - total_liabilities`)
- **Detail Arrays:**
  - `position_valuations[]`: local & base market values, FX rate applied, price age, staleness & override flags.
  - `share_class_navs[]`: opening/closing shares, allocation weights, class-specific accruals/liabilities, dealing NAV per share, closing NAV per share (`null` if `closing_shares == 0`), `zero_shares_flag`.
- **Controls & Diagnostics:**
  - `reconciliation`: `{reconciled, total_checks, passed_checks, breaks[]}`.
  - `warnings[]`: structured operational warnings (deferred flows, stale price overrides, zero-share classes).
  - `diagnostics`: counters (`positions_valued`, `stale_prices_detected`, `stale_price_overrides_used`, `flows_processed`, `flows_deferred_post_cutoff`, `flows_pending`, `flows_cancelled`, `zero_share_classes`).

## 4. Error Code Catalog

| Code | Exception Class | Trigger Condition |
|---|---|---|
| `NAV-1001` | `ConfigurationError` | Missing config file, malformed JSON, invalid currency precision, unsupported rounding mode, invalid cutoff time |
| `NAV-1002` | `ValidationError` | Schema violation, float input, duplicate IDs, negative non-derivative price, unknown share class, empty share classes, unsupported currency |
| `NAV-2001` | `MissingValuationError` | Non-cash held position has no `PriceRecord` on or before `valuation_date` |
| `NAV-2002` | `StalePriceError` | Price age exceeds `max_price_age_days` under `error` or unsatisfied `require_override` policy |
| `NAV-2003` | `FxMismatchError` | Missing FX conversion rate, or position currency differs from price currency |
| `NAV-2004` | `FxMismatchError` | FX rate age exceeds `max_price_age_days` |
| `NAV-2005` | `FxMismatchError` | Conflicting distinct FX rates for the same currency pair on the same date |
| `NAV-3001` | `FlowTimingError` | Capital flow `trade_date` is after `valuation_date` |
| `NAV-3002` | `FlowTimingError` | Capital flow `settlement_date` precedes `trade_date` |
| `NAV-3003` | `FlowTimingError` | Capital flow submitted after daily dealing cutoff (`pre_nav` rule or `reject` policy) |
| `NAV-3004` | `FlowTimingError` | Backdated capital flow (`trade_date < valuation_date`); requires restatement of `trade_date` |
| `NAV-3005` | `FlowTimingError` | Attempted subscription or amount-redemption with non-positive dealing NAV |
| `NAV-3006` | `FlowTimingError` | Redemption exceeds available shares in share class |
| `NAV-4001` | `LedgerReconciliationError` | One or more sub-ledger vs general ledger breaks exceed `ledger_tolerance` while `require_reconciliation=true` |
| `NAV-5001` | `ImmutabilityViolationError` | Attempt to modify, delete, overwrite, or tamper with a `frozen` snapshot or `audit_journal` entry |
| `NAV-5002` | `RestatementError` | Attempting to restate an unfrozen snapshot, missing restatement reason, mismatched fund/date, or lineage cycle |

## 5. Fixture Inventory (`data/fixtures/`)

- `reference_scenario.json` & `reference_expected.json`: Valid multi-currency reference scenario (`USD`, `EUR`, `JPY`, 2 share classes, accruals, subscriptions, redemptions, post-cutoff deferral) + exact hand-calculated oracle.
- `late_correction_bundle.json`: Revised reference bundle simulating a T+1 pricing vendor correction for restatement `v2`.
- `missing_valuation.json`: Unpriced equity position (`NAV-2001`).
- `fx_mismatch.json`: EUR position without EUR/USD FX rate (`NAV-2003`).
- `stale_price_exception.json`: 6-day-old bond quote with valuation committee override metadata (`NAV-2002` / override policy).
- `zero_shares_class.json`: Dormant class (`0` shares), full redemption to `0` shares, and seeded class starting from `0` shares.
- `flow_timing_boundary.json`: Pre-NAV flow submitted 1 second past cutoff (`NAV-3003`).
- `ledger_reconciliation_break.json`: `$500.00` cash sub-ledger vs GL break (`NAV-4001`).
- `invalid_inputs.json`: Unsupported currency `XYZ`, negative equity price, empty share classes (`NAV-1002`).
